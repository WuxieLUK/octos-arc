"""Task-agnostic regression verifier.

Given a requirement's test matrix and a generic change scope, this module
selects which test categories to rerun and which verification steps to perform.
It does not know task-specific paths, fields, routes, or test values.

The shadow helper only describes the verifier; it does not execute tests.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import re
from pathlib import Path
from typing import Any

from constraint_boundary_analyzer import ConstraintBoundaryReport, analyze_constraint_boundaries
from requirement_analyzer import RequirementIR, analyze_requirement
from requirement_decomposer import DecompositionPlan, decompose_requirement
from test_matrix_generator import TestCase, TestMatrixReport, generate_test_matrix


SCHEMA_VERSION = "1.0"
ANALYZER_NAME = "regression-verifier/1.0"


@dataclass
class SelectedRegressionCase:
    case_id: str
    category: str
    name: str
    objective: str
    reason: str
    priority: str = "required"


@dataclass
class RegressionVerificationPlan:
    id: str
    name: str = ""
    changed_layers: list[str] = field(default_factory=list)
    selected_cases: list[SelectedRegressionCase] = field(default_factory=list)
    verification_steps: list[str] = field(default_factory=list)
    coverage_gaps: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    schema_version: str = SCHEMA_VERSION
    analyzer: str = ANALYZER_NAME


@dataclass
class RegressionVerifierManifest:
    id: str
    name: str = ""
    status: str = "shadow"
    default_policy: str = "impact_driven"
    notes: str = "Regression selection is available through select_regression_tests() and is not injected into prompts yet."
    schema_version: str = SCHEMA_VERSION
    analyzer: str = ANALYZER_NAME


_LAYER_CATEGORY_MAP: dict[str, set[str]] = {
    "ui": {"happy_path", "boundary", "invalid_input", "equivalence", "error_handling"},
    "service": {"happy_path", "error_handling", "dependency", "authorization", "data_integrity", "persistence"},
    "validation": {"boundary", "invalid_input", "data_integrity", "equivalence", "error_handling"},
    "data": {"data_integrity", "persistence", "boundary", "invalid_input"},
    "state": {"happy_path", "persistence", "dependency", "error_handling"},
    "persistence": {"persistence", "data_integrity"},
    "error_handling": {"error_handling", "invalid_input", "boundary"},
    "integration": {"dependency", "happy_path"},
}

_BROAD_SCOPE_LAYERS = {"build", "requirement", "test_harness", "unknown"}


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def changed_layers_from_paths(paths: list[str]) -> list[str]:
    """Infer generic changed layers from changed file paths."""
    layers: set[str] = set()
    for raw_path in paths:
        path = _clean(raw_path).replace("\\", "/").lower()
        if not path:
            continue
        if "frontend" in path or path.endswith(".html") or path.endswith(".css"):
            layers.add("ui")
        if "backend" in path or path.endswith(".js") or path.endswith(".ts"):
            layers.add("service")
        if "package.json" in path or "build" in path or "lock" in path or path.endswith(".toml"):
            layers.add("build")
        if "requirements" in path or path.endswith(".yaml") or path.endswith(".yml"):
            layers.add("requirement")
        if "spec" in path or "test" in path:
            layers.add("test_harness")
        if not layers:
            layers.add("unknown")
    return sorted(layers)


def _coerce_analysis(
    source: RequirementIR | dict,
    constraints: ConstraintBoundaryReport | None,
    plan: DecompositionPlan | None,
) -> tuple[RequirementIR, ConstraintBoundaryReport, DecompositionPlan]:
    ir = source if isinstance(source, RequirementIR) else analyze_requirement(source)
    if constraints is None:
        constraints = analyze_constraint_boundaries(ir)
    if plan is None:
        plan = decompose_requirement(ir, constraints)
    return ir, constraints, plan


def _selected_categories(layers: list[str]) -> set[str]:
    categories: set[str] = set()
    for layer in layers:
        categories.update(_LAYER_CATEGORY_MAP.get(layer, set()))
    categories.add("happy_path")
    return categories


def format_regression_verification_plan(plan: RegressionVerificationPlan) -> str:
    """Render a small, task-agnostic regression verification plan for a prompt."""
    categories = sorted({case.category for case in plan.selected_cases})
    lines = [
        "[regression verification plan]",
        "Requirement scope: current requirement.",
        "Changed layers: " + (", ".join(plan.changed_layers) if plan.changed_layers else "full/unknown") + ".",
        "Regression categories: " + (", ".join(categories) if categories else "none") + ".",
    ]
    lines.extend(f"- {step}" for step in plan.verification_steps)
    lines.extend(f"Coverage gap: {gap}" for gap in plan.coverage_gaps)
    lines.extend(f"Warning: {warning}" for warning in plan.warnings)
    return "\n".join(lines) + "\n"


def select_regression_tests(
    source: RequirementIR | dict,
    changed_paths: list[str] | None = None,
    changed_layers: list[str] | None = None,
    constraints: ConstraintBoundaryReport | None = None,
    plan: DecompositionPlan | None = None,
) -> RegressionVerificationPlan:
    """Select regression tests for a generic change scope."""
    ir, constraints, plan = _coerce_analysis(source, constraints, plan)
    matrix: TestMatrixReport = generate_test_matrix(ir, constraints, plan)
    layers = list(changed_layers or []) if changed_layers is not None else changed_layers_from_paths(changed_paths or [])
    full_scope = not layers or any(layer in _BROAD_SCOPE_LAYERS for layer in layers)

    selected: list[SelectedRegressionCase] = []
    if full_scope:
        allowed_categories: set[str] | None = None
        reason = "No specific change scope was supplied, or the change affects build/requirements/tests; run the full matrix."
    else:
        allowed_categories = _selected_categories(layers)
        reason = f"Impact-driven selection for changed layers: {', '.join(layers)}."

    for case in matrix.test_cases:
        if allowed_categories is not None and case.category not in allowed_categories:
            continue
        selected.append(
            SelectedRegressionCase(
                case_id=case.case_id,
                category=case.category,
                name=case.name,
                objective=case.objective,
                reason=reason,
                priority=case.priority,
            )
        )

    if not selected and matrix.test_cases:
        selected = [
            SelectedRegressionCase(
                case_id=case.case_id,
                category=case.category,
                name=case.name,
                objective=case.objective,
                reason="Fallback: no impact-driven subset matched; rerun the full matrix.",
                priority=case.priority,
            )
            for case in matrix.test_cases
        ]

    steps = [
        "Run the project build/install step before behavior verification.",
        "Run the selected regression cases and compare their outcomes with the previously passing baseline.",
        "If the change scope is broad or ambiguous, rerun the full previously passing suite.",
        "Verify the application restores or preserves state correctly after the regression run.",
    ]
    gaps: list[str] = []
    warnings: list[str] = []
    if not selected:
        gaps.append("No test cases were available for regression selection.")
    if not layers:
        warnings.append("No changed layers were supplied; full-matrix regression was selected.")
    return RegressionVerificationPlan(
        id=ir.id,
        name=ir.name,
        changed_layers=layers,
        selected_cases=selected,
        verification_steps=steps,
        coverage_gaps=gaps,
        warnings=warnings,
    )


def build_regression_verifier_manifest(source: RequirementIR | dict) -> RegressionVerifierManifest:
    ir = source if isinstance(source, RequirementIR) else analyze_requirement(source)
    return RegressionVerifierManifest(id=ir.id, name=ir.name)


def write_regression_verifier_manifests(
    output_dir: Path,
    nodes: list[dict],
    enabled: bool = True,
) -> list[str]:
    """Write shadow verifier manifests under ``.arc/analysis/<node>.regression-verifier.json``."""
    if not enabled:
        return []
    analysis_dir = output_dir / ".arc" / "analysis"
    analysis_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for node in nodes:
        try:
            manifest = build_regression_verifier_manifest(node)
        except (TypeError, ValueError):
            continue
        safe_id = re.sub(r"[^A-Za-z0-9._-]+", "_", str(node.get("id") or "unknown")).strip("._")
        if not safe_id:
            safe_id = "unknown"
        path = analysis_dir / f"{safe_id}.regression-verifier.json"
        path.write_text(
            json.dumps(asdict(manifest), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        written.append(path.relative_to(output_dir).as_posix())
    return written
