"""Task-agnostic test matrix generator.

This module consumes the earlier requirement analyses and emits a generic,
executable-shaped test matrix.  It generates test cases from scenarios,
boundary categories, dependencies, persistence signals, and authorization
signals without knowing any task-specific field, route, button, or expected
value.

Apart from the optional ``write_test_matrix_analyses`` helper, this module
performs no I/O.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import re
from pathlib import Path
from typing import Any

from constraint_boundary_analyzer import (
    ConstraintBoundaryReport,
    analyze_constraint_boundaries,
)
from requirement_analyzer import (
    RequirementIR,
    analyze_requirement,
)
from requirement_decomposer import (
    DecompositionPlan,
    decompose_requirement,
)


SCHEMA_VERSION = "1.0"
ANALYZER_NAME = "test-matrix-generator/1.0"


@dataclass
class TestCase:
    case_id: str
    category: str
    name: str
    objective: str
    precondition: str = ""
    action: str = ""
    expected_outcome: str = ""
    source: str = "derived"
    priority: str = "required"
    evidence: str = ""


@dataclass
class TestMatrixReport:
    id: str
    name: str = ""
    test_cases: list[TestCase] = field(default_factory=list)
    coverage_summary: dict[str, int] = field(default_factory=dict)
    gaps: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    schema_version: str = SCHEMA_VERSION
    analyzer: str = ANALYZER_NAME


_AUTH_EN = (
    "authorize", "authorized", "authorization", "permission", "role",
    "forbidden", "unauthorized", "owner", "access control",
)
_AUTH_ZH = ("授权", "权限", "角色", "禁止访问", "未授权", "所有者", "访问控制")


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _join(values: list[str], fallback: str = "") -> str:
    return " | ".join(_clean(value) for value in values if _clean(value)) or fallback


def _has_any(text: str, phrases: tuple[str, ...]) -> bool:
    lower = text.lower()
    return any(phrase in lower for phrase in phrases)


def _coerce_inputs(
    source: RequirementIR | dict,
    constraints: ConstraintBoundaryReport | None,
    plan: DecompositionPlan | None,
) -> tuple[RequirementIR, ConstraintBoundaryReport, DecompositionPlan]:
    if isinstance(source, RequirementIR):
        ir = source
    elif isinstance(source, dict):
        ir = analyze_requirement(source)
    else:
        raise TypeError("source must be a RequirementIR or requirement node dict")

    if constraints is None:
        constraints = analyze_constraint_boundaries(ir)
    if not isinstance(constraints, ConstraintBoundaryReport):
        raise TypeError("constraints must be a ConstraintBoundaryReport or None")

    if plan is None:
        plan = decompose_requirement(ir, constraints)
    if not isinstance(plan, DecompositionPlan):
        raise TypeError("plan must be a DecompositionPlan or None")
    return ir, constraints, plan


def _append_test_case(
    cases: list[TestCase],
    seen: set[tuple[str, str]],
    category: str,
    name: str,
    objective: str,
    precondition: str = "",
    action: str = "",
    expected_outcome: str = "",
    source: str = "derived",
    priority: str = "required",
    evidence: str = "",
) -> None:
    key = (category, _clean(name).lower(), _clean(objective).lower())
    if key in seen:
        return
    seen.add(key)
    cases.append(
        TestCase(
            case_id=f"test-{len(cases) + 1}",
            category=category,
            name=_clean(name),
            objective=_clean(objective),
            precondition=_clean(precondition),
            action=_clean(action),
            expected_outcome=_clean(expected_outcome),
            source=source,
            priority=priority,
            evidence=_clean(evidence),
        )
    )


def _scenario_cases(ir: RequirementIR, cases: list[TestCase], seen: set[tuple[str, str]]) -> None:
    for scenario_index, scenario in enumerate(ir.scenarios, 1):
        _append_test_case(
            cases,
            seen,
            "happy_path",
            f"Scenario {scenario_index}: {scenario.name}",
            "Verify the primary behavior described by the scenario.",
            precondition=_join(scenario.given, "The required preconditions are satisfied."),
            action=_join(scenario.when, "The actor performs the primary action."),
            expected_outcome=_join(scenario.then, "The expected observable outcome is produced."),
            source="scenario",
            priority="required",
            evidence=f"Scenario '{scenario.name}'",
        )


def _boundary_cases(
    constraints: ConstraintBoundaryReport,
    cases: list[TestCase],
    seen: set[tuple[str, str]],
) -> None:
    for boundary_case in constraints.cases:
        category = boundary_case.category
        if category in {"happy_path"}:
            continue
        mapped = _map_boundary_category(category)
        _append_test_case(
            cases,
            seen,
            mapped,
            f"Boundary case: {category}",
            boundary_case.scenario_hint,
            action="Apply the boundary condition described by the requirement.",
            expected_outcome="Verify the behavior matches the requirement without unintended side effects.",
            source="constraint",
            priority=boundary_case.priority,
            evidence=boundary_case.scenario_hint,
        )


def _map_boundary_category(category: str) -> str:
    if category in {"missing", "empty", "whitespace_only"}:
        return "negative_or_boundary"
    if category in {
        "below_lower", "above_upper", "below_minimum", "above_maximum",
        "below_exact", "above_exact",
    }:
        return "boundary"
    if category in {"invalid_format", "non_numeric"}:
        return "invalid_input"
    if category in {"duplicate", "unique"}:
        return "data_integrity"
    if category in {"case_equivalent", "case_distinct", "whitespace_normalized"}:
        return "equivalence"
    if category in {"dependency_met", "dependency_missing"}:
        return "dependency"
    if category == "persistence_reload":
        return "persistence"
    if category == "expected_error":
        return "error_handling"
    return category


def _derived_cases(
    ir: RequirementIR,
    constraints: ConstraintBoundaryReport,
    plan: DecompositionPlan,
    cases: list[TestCase],
    seen: set[tuple[str, str]],
) -> None:
    text = "\n".join(
        [ir.description] + [step.content for scenario in ir.scenarios for step in scenario.steps]
    )
    case_categories = {case.category for case in constraints.cases}
    rule_categories = {rule.category for rule in constraints.rules}
    area_categories = {area.category for area in plan.test_areas}

    if not ir.scenarios:
        _append_test_case(
            cases,
            seen,
            "happy_path",
            "Primary happy path",
            "Verify the main action and its observable outcome.",
            precondition="The application is reachable and required preconditions are satisfied.",
            action="Execute the primary action from the requirement.",
            expected_outcome="The expected observable outcome is produced.",
            source="derived",
            priority="required",
        )

    if ir.dependencies or {"dependency_met", "dependency_missing"} & case_categories:
        _append_test_case(
            cases,
            seen,
            "dependency",
            "Dependent precondition is missing",
            "Verify the action fails safely when a required dependency is not satisfied.",
            precondition="A required dependency is absent.",
            action="Execute the dependent action.",
            expected_outcome="The action is rejected or handled without corrupting state.",
            source="derived",
            priority="required",
            evidence=_join(ir.dependencies),
        )

    if {"persistence_reload"} & case_categories or "persistence" in area_categories:
        _append_test_case(
            cases,
            seen,
            "persistence",
            "State survives reload",
            "Verify durable state remains correct after reload/restart.",
            precondition="A successful action has already completed.",
            action="Reload or restart the application and re-read the state.",
            expected_outcome="The resulting state remains correct.",
            source="derived",
            priority="recommended",
        )

    if _has_any(text, _AUTH_EN) or _has_any(text, _AUTH_ZH):
        _append_test_case(
            cases,
            seen,
            "authorization",
            "Unauthorized access is rejected",
            "Verify an actor without the required permission cannot perform the action.",
            precondition="An actor lacks the required permission.",
            action="Attempt the protected action.",
            expected_outcome="The action is rejected and no protected state is changed.",
            source="derived",
            priority="required",
        )

    if not ir.error_cases and "error" not in rule_categories and not any(
        c in case_categories for c in {"expected_error", "invalid_format", "missing", "empty"}
    ):
        _append_test_case(
            cases,
            seen,
            "negative_or_boundary",
            "Generic invalid input",
            "Verify invalid, empty, or out-of-range input is rejected.",
            precondition="The application is reachable.",
            action="Submit an invalid or empty value.",
            expected_outcome="A visible error is produced and no state is changed.",
            source="derived",
            priority="recommended",
        )


def _coverage_summary(cases: list[TestCase]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for case in cases:
        summary[case.category] = summary.get(case.category, 0) + 1
    return summary


def _derive_gaps(
    ir: RequirementIR,
    summary: dict[str, int],
) -> tuple[list[str], list[str]]:
    gaps: list[str] = []
    warnings: list[str] = []
    if summary.get("happy_path", 0) == 0:
        gaps.append("No happy-path test was generated; add a primary success scenario.")
    if summary.get("negative_or_boundary", 0) == 0 and summary.get("invalid_input", 0) == 0:
        gaps.append("No negative/boundary test was generated; add invalid and boundary cases.")
    if ir.dependencies and summary.get("dependency", 0) == 0:
        gaps.append("Dependencies were declared but no dependency test was generated.")
    if not ir.scenarios:
        warnings.append("No structured scenario was found; test cases are derived from free-text signals only.")
    return gaps, warnings


def generate_test_matrix(
    source: RequirementIR | dict,
    constraints: ConstraintBoundaryReport | None = None,
    plan: DecompositionPlan | None = None,
) -> TestMatrixReport:
    """Return a generic, executable-shaped test matrix for one requirement."""
    ir, constraints, plan = _coerce_inputs(source, constraints, plan)
    cases: list[TestCase] = []
    seen: set[tuple[str, str]] = set()

    _scenario_cases(ir, cases, seen)
    _boundary_cases(constraints, cases, seen)
    _derived_cases(ir, constraints, plan, cases, seen)
    summary = _coverage_summary(cases)
    gaps, warnings = _derive_gaps(ir, summary)
    return TestMatrixReport(
        id=ir.id,
        name=ir.name,
        test_cases=cases,
        coverage_summary=summary,
        gaps=gaps,
        warnings=warnings,
    )


def test_matrix_to_dict(
    source: RequirementIR | dict,
    constraints: ConstraintBoundaryReport | None = None,
    plan: DecompositionPlan | None = None,
) -> dict[str, Any]:
    """Analyze a requirement and return a JSON-serializable dictionary."""
    return asdict(generate_test_matrix(source, constraints, plan))


def write_test_matrix_analyses(
    output_dir: Path,
    nodes: list[dict],
    enabled: bool = True,
) -> list[str]:
    """Write shadow test matrices under ``.arc/analysis/<node>.test-matrix.json``.

    These files are observational only.  They must not be fed back into prompts
    until a later module integrates them explicitly.
    """
    if not enabled:
        return []
    analysis_dir = output_dir / ".arc" / "analysis"
    analysis_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for node in nodes:
        try:
            report = generate_test_matrix(node)
        except (TypeError, ValueError):
            continue
        safe_id = re.sub(r"[^A-Za-z0-9._-]+", "_", str(node.get("id") or "unknown")).strip("._")
        if not safe_id:
            safe_id = "unknown"
        path = analysis_dir / f"{safe_id}.test-matrix.json"
        path.write_text(
            json.dumps(asdict(report), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        written.append(path.relative_to(output_dir).as_posix())
    return written
