"""Task-agnostic test runner primitives.

The module provides a small, dependency-free subprocess executor for tests and
a shadow manifest describing what the runner would execute for each node.

It deliberately does not know the project's test framework, package manager,
browser, or task-specific commands.  A later repair/failure module can call
``run_test_command`` with an explicitly supplied command.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from test_matrix_generator import generate_test_matrix


SCHEMA_VERSION = "1.0"
ANALYZER_NAME = "test-runner/1.0"


@dataclass
class TestRunResult:
    command: list[str]
    cwd: str
    timeout_seconds: int
    return_code: int | None
    timed_out: bool
    passed: bool
    duration_seconds: float
    stdout_tail: str
    stderr_tail: str
    output_digest: str
    error_signature: str


@dataclass
class TestRunnerManifest:
    id: str
    name: str = ""
    status: str = "shadow"
    test_case_count: int = 0
    categories: list[str] = field(default_factory=list)
    supported_modes: list[str] = field(default_factory=lambda: ["exit_code"])
    default_timeout_seconds: int = 300
    capture_limit: int = 20000
    notes: str = "No test command is supplied by this module; a later repair loop should invoke run_test_command explicitly."
    schema_version: str = SCHEMA_VERSION
    analyzer: str = ANALYZER_NAME


def _tail(text: str, limit: int) -> str:
    lines = [line for line in (text or "").splitlines() if line.strip()]
    return "\n".join(lines[-limit:])


def _output_digest(stdout: str, stderr: str) -> str:
    payload = f"{stdout}\n{stderr}".encode("utf-8", errors="replace")
    return hashlib.sha256(payload).hexdigest()


def _error_signature(return_code: int | None, timed_out: bool, stdout: str, stderr: str) -> str:
    if timed_out:
        return "timeout"
    if return_code == 0:
        return "ok"
    lines = [
        line.strip()
        for line in ((stderr or "") + "\n" + (stdout or "")).splitlines()
        if line.strip()
    ]
    return " | ".join(lines[-3:]) or f"exit_code:{return_code}"


def run_test_command(
    command: list[str],
    cwd: str | Path | None = None,
    timeout_seconds: int = 300,
    env: dict[str, str] | None = None,
    capture_limit: int = 20000,
) -> TestRunResult:
    """Run a caller-supplied test command and normalize its result.

    This function intentionally accepts no test framework assumptions.  It
    treats a zero exit code as pass and a non-zero exit code or timeout as fail.
    """
    if not command:
        raise ValueError("command must not be empty")
    normalized_command = [str(part) for part in command]
    cwd_str = str(cwd) if cwd is not None else str(Path.cwd())
    merged_env = os.environ.copy()
    if env:
        merged_env.update({str(key): str(value) for key, value in env.items()})

    started = time.monotonic()
    stdout = ""
    stderr = ""
    return_code: int | None = None
    timed_out = False
    try:
        completed = subprocess.run(
            normalized_command,
            cwd=cwd_str,
            env=merged_env,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout_seconds,
            check=False,
        )
        return_code = completed.returncode
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        return_code = None
        stdout = (exc.stdout or b"").decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = (exc.stderr or b"").decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")

    duration = time.monotonic() - started
    return TestRunResult(
        command=normalized_command,
        cwd=cwd_str,
        timeout_seconds=timeout_seconds,
        return_code=return_code,
        timed_out=timed_out,
        passed=(return_code == 0 and not timed_out),
        duration_seconds=duration,
        stdout_tail=_tail(stdout, capture_limit)[-capture_limit:],
        stderr_tail=_tail(stderr, capture_limit)[-capture_limit:],
        output_digest=_output_digest(stdout, stderr),
        error_signature=_error_signature(return_code, timed_out, stdout, stderr),
    )


def build_runner_manifest(source: Any) -> TestRunnerManifest:
    """Build a shadow manifest for one requirement node."""
    matrix = generate_test_matrix(source)
    return TestRunnerManifest(
        id=matrix.id,
        name=matrix.name,
        test_case_count=len(matrix.test_cases),
        categories=sorted(matrix.coverage_summary.keys()),
    )


def write_test_runner_manifests(
    output_dir: Path,
    nodes: list[dict],
    enabled: bool = True,
) -> list[str]:
    """Write shadow runner manifests under ``.arc/analysis/<node>.test-runner.json``."""
    if not enabled:
        return []
    analysis_dir = output_dir / ".arc" / "analysis"
    analysis_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for node in nodes:
        try:
            manifest = build_runner_manifest(node)
        except (TypeError, ValueError):
            continue
        safe_id = re.sub(r"[^A-Za-z0-9._-]+", "_", str(node.get("id") or "unknown")).strip("._")
        if not safe_id:
            safe_id = "unknown"
        path = analysis_dir / f"{safe_id}.test-runner.json"
        path.write_text(
            json.dumps(asdict(manifest), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        written.append(path.relative_to(output_dir).as_posix())
    return written
