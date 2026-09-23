"""Task-agnostic final verification gate.

This module derives the checks that should be true before an agent may claim
completion, then evaluates caller-supplied evidence.  It does not know any
task-specific field, route, button, or test value.

The shadow helper only describes the verifier; it does not execute tests.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import re
from pathlib import Path
from typing import Any

from requirement_analyzer import RequirementIR, analyze_requirement
from test_matrix_generator import TestMatrixReport, generate_test_matrix


SCHEMA_VERSION = "1.0"
ANALYZER_NAME = "final-verification/1.0"


@dataclass
class VerificationCheck:
    check_id: str
    category: str
    description: str
    required: bool
    passed: bool
    evidence: str = ""


@dataclass
class FinalVerificationReport:
    id: str
    name: str = ""
    passed: bool = False
    status: str = "pending"
    checks: list[VerificationCheck] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    schema_version: str = SCHEMA_VERSION
    analyzer: str = ANALYZER_NAME


@dataclass
class FinalVerificationManifest:
    id: str
    name: str = ""
    status: str = "shadow"
    default_policy: str = "all_required_checks_must_pass"
    notes: str = "Final-verification gating is available through evaluate_final_verification() and is not injected into prompts yet."
    schema_version: str = SCHEMA_VERSION
    analyzer: str = ANALYZER_NAME


_PERSISTENCE_EN = ("persist", "durable", "storage", "restart", "refresh", "reload", "save")
_PERSISTENCE_ZH = ("持久化", "保存", "存储", "重启", "刷新", "重新加载", "恢复")


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _has_any(text: str, phrases: tuple[str, ...]) -> bool:
    lower = text.lower()
    return any(phrase in lower for phrase in phrases)


def _requirement_text(ir: RequirementIR) -> str:
    return "\n".join(
        [ir.description] + [step.content for scenario in ir.scenarios for step in scenario.steps]
    )


def _derived_check_ids(ir: RequirementIR, matrix: TestMatrixReport) -> set[str]:
    ids = {"build", "startup", "happy_path", "regression", "no_protected_test_modifications"}
    categories = set(matrix.coverage_summary.keys())
    if (
        ir.input_rules
        or ir.boundaries
        or ir.error_cases
        or any(
            category in categories
            for category in {"boundary", "invalid_input", "negative_or_boundary", "error_handling"}
        )
    ):
        ids.add("negative_or_boundary")
    text = _requirement_text(ir)
    if _has_any(text, _PERSISTENCE_EN) or _has_any(text, _PERSISTENCE_ZH) or "persistence" in categories:
        ids.add("persistence")
    if ir.dependencies or "dependency" in categories:
        ids.add("dependency")
    return ids


def _checks(ir: RequirementIR, matrix: TestMatrixReport, evidence: dict[str, bool]) -> list[VerificationCheck]:
    required_ids = _derived_check_ids(ir, matrix)
    descriptions = {
        "build": ("build", "Build/install succeeds cleanly."),
        "startup": ("startup", "The application/service starts and its entry point is reachable."),
        "happy_path": ("happy_path", "The primary successful flow produces its observable outcome."),
        "negative_or_boundary": ("negative", "Invalid, empty, and boundary cases are rejected without side effects."),
        "persistence": ("persistence", "Durable state remains correct after reload/restart."),
        "dependency": ("dependency", "Dependent actions behave correctly with and without required preconditions."),
        "regression": ("regression", "Previously passing cases still pass."),
        "no_protected_test_modifications": ("integrity", "Protected requirement/test ground truth is unchanged."),
    }
    checks: list[VerificationCheck] = []
    for check_id, (category, description) in descriptions.items():
        required = check_id in required_ids
        passed = bool(evidence.get(check_id, False))
        checks.append(
            VerificationCheck(
                check_id=check_id,
                category=category,
                description=description,
                required=required,
                passed=passed,
                evidence="provided" if check_id in evidence else "missing",
            )
        )
    return checks


def evaluate_final_verification(
    source: RequirementIR | dict,
    evidence: dict[str, bool] | None = None,
    matrix: TestMatrixReport | None = None,
) -> FinalVerificationReport:
    """Evaluate whether the generic completion gate is satisfied."""
    ir = source if isinstance(source, RequirementIR) else analyze_requirement(source)
    if matrix is None:
        matrix = generate_test_matrix(ir)
    evidence = evidence or {}
    checks = _checks(ir, matrix, evidence)
    passed = all(check.passed for check in checks if check.required)
    gaps = [
        f"Missing required evidence for {check.check_id}: {check.description}"
        for check in checks
        if check.required and not check.passed
    ]
    warnings = [
        f"Missing optional evidence for {check.check_id}: {check.description}"
        for check in checks
        if not check.required and not check.passed
    ]
    return FinalVerificationReport(
        id=ir.id,
        name=ir.name,
        passed=passed,
        status="passed" if passed else "pending",
        checks=checks,
        gaps=gaps,
        warnings=warnings,
    )


def build_final_verification_manifest(source: RequirementIR | dict) -> FinalVerificationManifest:
    ir = source if isinstance(source, RequirementIR) else analyze_requirement(source)
    return FinalVerificationManifest(id=ir.id, name=ir.name)


def write_final_verification_manifests(
    output_dir: Path,
    nodes: list[dict],
    enabled: bool = True,
) -> list[str]:
    """Write shadow final-verification manifests under ``.arc/analysis/<node>.final-verification.json``."""
    if not enabled:
        return []
    analysis_dir = output_dir / ".arc" / "analysis"
    analysis_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for node in nodes:
        try:
            manifest = build_final_verification_manifest(node)
        except (TypeError, ValueError):
            continue
        safe_id = re.sub(r"[^A-Za-z0-9._-]+", "_", str(node.get("id") or "unknown")).strip("._")
        if not safe_id:
            safe_id = "unknown"
        path = analysis_dir / f"{safe_id}.final-verification.json"
        path.write_text(
            json.dumps(asdict(manifest), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        written.append(path.relative_to(output_dir).as_posix())
    return written
