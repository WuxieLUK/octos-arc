"""Task-agnostic environment detector.

This module reports generic runtime capabilities: Python/Node/npm availability,
git presence, whether a model endpoint is configured, and selected ports.  It
never exposes secrets and does not inspect task-specific paths or files.

The optional ``write_environment_report`` helper is shadow-only.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import os
import platform
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "1.0"
ANALYZER_NAME = "environment-detector/1.0"


@dataclass
class CommandAvailability:
    name: str
    available: bool


@dataclass
class EnvironmentReport:
    platform: str
    python_version: str
    node_version: str | None
    npm_version: str | None
    git_available: bool
    playwright_root_configured: bool
    model_configured: bool
    base_url_configured: bool
    web_port: int | None
    smoke_port: int | None
    commands: list[CommandAvailability] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    schema_version: str = SCHEMA_VERSION
    analyzer: str = ANALYZER_NAME


@dataclass
class EnvironmentExecutionPolicy:
    """Task-agnostic execution decisions derived from runtime capabilities."""

    shell_available: bool
    node_available: bool
    npm_available: bool
    disable_shell: bool
    prefer_static_build: bool
    warnings: list[str] = field(default_factory=list)


def _run_version(command: str, args: list[str]) -> str | None:
    if shutil.which(command) is None:
        return None
    try:
        completed = subprocess.run(
            [command, *args],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    output = ((completed.stdout or "") + (completed.stderr or "")).strip()
    return output.splitlines()[0][:120] if output else None


def _env_int(name: str) -> int | None:
    value = os.environ.get(name)
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _available_commands(report: EnvironmentReport | None = None) -> dict[str, bool]:
    selected = report or detect_environment()
    return {command.name: command.available for command in selected.commands}


def environment_execution_policy(
    report: EnvironmentReport | None = None,
) -> EnvironmentExecutionPolicy:
    """Derive generic tooling decisions from runtime capabilities."""
    selected = report or detect_environment()
    commands = _available_commands(selected)
    shell_available = commands.get("bash", False) or commands.get("sh", False)
    node_available = commands.get("node", False)
    npm_available = commands.get("npm", False)
    warnings = list(selected.warnings)
    if not shell_available:
        warnings.append("No POSIX shell is available; avoid shell commands and use file tools for edits.")
    if node_available and not npm_available:
        warnings.append("Node is available but npm is not; avoid npm install/build and prefer already-installed runtimes.")
    return EnvironmentExecutionPolicy(
        shell_available=shell_available,
        node_available=node_available,
        npm_available=npm_available,
        disable_shell=not shell_available,
        prefer_static_build=not node_available or not npm_available,
        warnings=warnings,
    )


def format_environment_brief(report: EnvironmentReport | None = None) -> str:
    """Render a small, secret-free environment brief for model prompts."""
    selected = report or detect_environment()
    policy = environment_execution_policy(selected)
    available = [name for name, ok in _available_commands(selected).items() if ok]
    missing = [name for name, ok in _available_commands(selected).items() if not ok]
    lines = [
        "[environment brief]",
        f"Platform: {selected.platform}; Python {selected.python_version}; Node {selected.node_version or 'unavailable'}; "
        f"npm {selected.npm_version or 'unavailable'}.",
        "Available commands: " + (", ".join(available) or "none") + ".",
    ]
    if missing:
        lines.append("Unavailable commands: " + ", ".join(missing) + ".")
    if policy.prefer_static_build:
        lines.append("Build policy: do not plan npm install/build; prefer a static or already-installed runtime.")
    if policy.disable_shell:
        lines.append("Shell policy: shell execution is unavailable; edit files with file tools and let the harness verify.")
    for warning in selected.warnings:
        lines.append(f"Warning: {warning}")
    return "\n".join(lines) + "\n"


def detect_environment() -> EnvironmentReport:
    """Detect generic runtime capabilities without exposing secrets."""
    command_names = ["python", "node", "npm", "npx", "git", "curl", "bash", "cargo", "docker"]
    commands = [
        CommandAvailability(name=name, available=shutil.which(name) is not None)
        for name in command_names
    ]
    node_version = _run_version("node", ["--version"])
    npm_version = _run_version("npm", ["--version"])
    model_configured = bool(os.environ.get("OPENAI_API_KEY") or os.environ.get("ARCBENCH_API_KEY"))
    base_url = os.environ.get("OPENAI_BASE_URL", "")
    base_url_configured = bool(base_url.startswith("http"))
    playwright_root_configured = bool(os.environ.get("OCTOS_ARC_PLAYWRIGHT_ROOT"))

    warnings: list[str] = []
    if shutil.which("node") is None or shutil.which("npm") is None:
        warnings.append("Node and/or npm are unavailable; browser-based build/verification may not run.")
    if shutil.which("git") is None:
        warnings.append("Git is unavailable; snapshot/regression operations may not work.")

    return EnvironmentReport(
        platform=platform.system(),
        python_version=platform.python_version(),
        node_version=node_version,
        npm_version=npm_version,
        git_available=shutil.which("git") is not None,
        playwright_root_configured=playwright_root_configured,
        model_configured=model_configured,
        base_url_configured=base_url_configured,
        web_port=_env_int("ARCBENCH_WEB_PORT") or _env_int("ARC_WEB_PORT"),
        smoke_port=_env_int("OCTOS_SMOKE_PORT"),
        commands=commands,
        warnings=warnings,
    )


def write_environment_report(output_dir: Path, enabled: bool = True) -> list[str]:
    """Write a run-level environment report under ``.arc/analysis/environment.json``."""
    if not enabled:
        return []
    analysis_dir = output_dir / ".arc" / "analysis"
    analysis_dir.mkdir(parents=True, exist_ok=True)
    report = detect_environment()
    path = analysis_dir / "environment.json"
    path.write_text(
        json.dumps(asdict(report), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return [path.relative_to(output_dir).as_posix()]
