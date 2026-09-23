"""Task-agnostic requirement decomposer.

This module turns a ``RequirementIR`` plus its ``ConstraintBoundaryReport`` into
a generic decomposition:

    requirement -> interface contract -> implementation units -> tests -> verification

It contains no task names, field names, routes, buttons, or expected test values.
The decomposition is deterministic and, apart from the optional
``write_decomposition_analyses`` helper, performs no I/O.
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


SCHEMA_VERSION = "1.0"
ANALYZER_NAME = "requirement-decomposer/1.0"


@dataclass
class InterfaceContract:
    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    state_changes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)


@dataclass
class ImplementationUnit:
    unit_id: str
    layer: str
    rationale: str
    evidence: str
    deliverable_hint: str


@dataclass
class TestArea:
    area_id: str
    category: str
    hint: str
    source: str = "derived"


@dataclass
class VerificationStep:
    step_id: str
    phase: str
    action: str
    success_criterion: str


@dataclass
class DecompositionPlan:
    id: str
    name: str = ""
    interface_contract: InterfaceContract = field(default_factory=InterfaceContract)
    implementation_units: list[ImplementationUnit] = field(default_factory=list)
    test_areas: list[TestArea] = field(default_factory=list)
    verification_plan: list[VerificationStep] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    schema_version: str = SCHEMA_VERSION
    analyzer: str = ANALYZER_NAME


_UI_EN = ("display", "show", "visible", "page", "button", "click", "navigate", "form")
_UI_ZH = ("显示", "页面", "按钮", "点击", "导航", "界面", "表单")

_SERVICE_EN = ("request", "response", "api", "server", "endpoint", "submit", "service", "http")
_SERVICE_ZH = ("请求", "响应", "接口", "服务端", "服务", "提交", "http")

_STATE_EN = ("state", "session", "current", "remain", "transition", "status")
_STATE_ZH = ("状态", "会话", "当前", "保持", "切换", "恢复")

_PERSISTENCE_EN = ("persist", "durable", "storage", "restart", "refresh", "reload", "save")
_PERSISTENCE_ZH = ("持久化", "保存", "存储", "重启", "刷新", "重新加载", "恢复")

_VALIDATION_EN = (
    "valid", "invalid", "required", "optional", "format", "pattern", "length",
    "unique", "duplicate", "reject",
)
_VALIDATION_ZH = ("校验", "有效", "无效", "必填", "选填", "格式", "长度", "唯一", "重复", "拒绝")

_DATA_EN = ("record", "entity", "data", "store", "collection", "item", "object")
_DATA_ZH = ("记录", "实体", "数据", "存储", "集合", "条目", "对象")

_INTEGRATION_EN = ("dependency", "depends", "requires", "integration", "parent", "existing")
_INTEGRATION_ZH = ("依赖", "依赖关系", "集成", "已有", "前置")


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _has_any(text: str, phrases: tuple[str, ...]) -> bool:
    lower = text.lower()
    return any(phrase in lower for phrase in phrases)


def _coerce_inputs(
    source: RequirementIR | dict,
    constraints: ConstraintBoundaryReport | None,
) -> tuple[RequirementIR, ConstraintBoundaryReport]:
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
    return ir, constraints


def _requirement_text(ir: RequirementIR) -> str:
    return "\n".join(
        [ir.description] + [step.content for scenario in ir.scenarios for step in scenario.steps]
    )


def _build_interface_contract(ir: RequirementIR) -> InterfaceContract:
    return InterfaceContract(
        inputs=list(ir.input_rules),
        outputs=list(ir.output_rules),
        state_changes=list(ir.states),
        errors=list(ir.error_cases),
        dependencies=list(ir.dependencies),
    )


def _add_unit(
    units: list[ImplementationUnit],
    layer: str,
    rationale: str,
    evidence: str,
    deliverable_hint: str,
) -> None:
    units.append(
        ImplementationUnit(
            unit_id=f"impl-{len(units) + 1}",
            layer=layer,
            rationale=rationale,
            evidence=evidence,
            deliverable_hint=deliverable_hint,
        )
    )


def _build_implementation_units(
    ir: RequirementIR,
    constraints: ConstraintBoundaryReport,
) -> list[ImplementationUnit]:
    units: list[ImplementationUnit] = []
    text = _requirement_text(ir)
    rule_categories = {rule.category for rule in constraints.rules}

    if ir.scenarios or ir.output_rules or _has_any(text, _UI_EN) or _has_any(text, _UI_ZH):
        _add_unit(
            units,
            "ui",
            "Expose the user-visible entry points, controls, and observable outcomes required by the scenarios.",
            "Scenarios or output rules were detected.",
            "Implement the user-facing surface so each observable outcome is visible and reachable.",
        )

    if ir.dependencies or _has_any(text, _SERVICE_EN) or _has_any(text, _SERVICE_ZH):
        _add_unit(
            units,
            "service",
            "Define the boundary that receives actions and returns responses without leaking internal state.",
            "Request/service signals or dependencies were detected.",
            "Implement the service/request boundary and route each action to the correct state transition.",
        )

    if ir.input_rules or ir.boundaries or "format" in rule_categories or "numeric" in rule_categories:
        _add_unit(
            units,
            "validation",
            "Enforce every declared input, format, length, and boundary constraint consistently.",
            "Input rules or numeric boundaries were detected.",
            "Implement validation before state mutation and return a visible error for rejected values.",
        )

    if ir.states or ir.dependencies or _has_any(text, _STATE_EN) or _has_any(text, _STATE_ZH):
        _add_unit(
            units,
            "state",
            "Model the required states and transitions so actions produce the expected next state.",
            "State, session, or dependency signals were detected.",
            "Implement state transitions idempotently and avoid partial state changes.",
        )

    if _has_any(text, _PERSISTENCE_EN) or _has_any(text, _PERSISTENCE_ZH):
        _add_unit(
            units,
            "persistence",
            "Make durable state survive reload/restart as required by the persistence clauses.",
            "Persistence or reload signals were detected.",
            "Use a durable store and verify state survives an application reload.",
        )

    if ir.error_cases or "error" in rule_categories:
        _add_unit(
            units,
            "error_handling",
            "Handle declared failure conditions without side effects and with visible feedback.",
            "Error or negative clauses were detected.",
            "Implement error paths so rejected actions do not create or change state.",
        )

    if ir.entities or "unique" in rule_categories or _has_any(text, _DATA_EN) or _has_any(text, _DATA_ZH):
        _add_unit(
            units,
            "data",
            "Represent the entities and data integrity constraints required by the domain.",
            "Entity, data, or uniqueness signals were detected.",
            "Implement the data model and enforce uniqueness/integrity at the data boundary.",
        )

    if ir.dependencies or _has_any(text, _INTEGRATION_EN) or _has_any(text, _INTEGRATION_ZH):
        _add_unit(
            units,
            "integration",
            "Connect this requirement to declared dependencies without breaking already-working behavior.",
            "Dependency or integration signals were detected.",
            "Reuse dependency contracts and verify dependent preconditions before acting.",
        )

    return units


def _build_test_areas(
    ir: RequirementIR,
    constraints: ConstraintBoundaryReport,
) -> list[TestArea]:
    areas: list[TestArea] = []
    case_categories = {case.category for case in constraints.cases}

    def add(category: str, hint: str) -> None:
        areas.append(
            TestArea(
                area_id=f"test-{len(areas) + 1}",
                category=category,
                hint=hint,
            )
        )

    if "happy_path" in case_categories or ir.scenarios:
        add("happy_path", "Exercise the primary successful flow and verify its observable outcome.")

    negative_categories = {
        "missing", "below_lower", "above_upper", "below_minimum", "above_maximum",
        "invalid_format", "non_numeric", "empty", "whitespace_only", "expected_error",
    }
    if case_categories & negative_categories or ir.error_cases:
        add("boundary_and_negative", "Exercise invalid, empty, and off-by-one cases and verify visible rejection without side effects.")

    if {"duplicate", "unique"} & case_categories or "unique" in {rule.category for rule in constraints.rules}:
        add("data_integrity", "Verify uniqueness, duplicate rejection, and data-integrity constraints.")

    if {"case_equivalent", "case_distinct", "whitespace_normalized"} & case_categories:
        add("equivalence", "Verify equivalent inputs such as case and whitespace variants are treated consistently.")

    if {"dependency_met", "dependency_missing"} & case_categories or ir.dependencies:
        add("dependency", "Verify behavior with dependencies satisfied and with each required precondition missing.")

    if "persistence_reload" in case_categories:
        add("persistence", "Verify durable state remains correct after reload/restart.")

    if not areas:
        add("unspecified_acceptance", "No structured acceptance signals were found; derive scenarios directly from the requirement text.")
    return areas


def _build_verification_plan(
    ir: RequirementIR,
    constraints: ConstraintBoundaryReport,
) -> list[VerificationStep]:
    steps: list[VerificationStep] = []
    case_categories = {case.category for case in constraints.cases}

    def add(phase: str, action: str, criterion: str) -> None:
        steps.append(
            VerificationStep(
                step_id=f"verify-{len(steps) + 1}",
                phase=phase,
                action=action,
                success_criterion=criterion,
            )
        )

    add("review", "Inspect the generated or current code for the declared interface and constraints.", "Every implementation unit has an identifiable code location.")
    add("build", "Run the project's build/install command.", "The build or install step exits cleanly.")
    add("startup", "Start the application/service and wait for its entry point.", "The entry point becomes reachable without an early exit.")
    if "happy_path" in case_categories or ir.scenarios:
        add("happy_path", "Exercise the primary happy path from the decomposition.", "The expected observable outcome is produced.")
    if case_categories & {"missing", "below_lower", "above_upper", "invalid_format", "non_numeric", "empty", "expected_error"} or ir.error_cases:
        add("negative", "Run the boundary/negative cases.", "Each invalid case is rejected visibly and no state is mutated.")
    if ir.dependencies or {"dependency_met", "dependency_missing"} & case_categories:
        add("dependency", "Run dependent cases with and without required preconditions.", "Missing dependencies fail safely and satisfied dependencies succeed.")
    if "persistence_reload" in case_categories:
        add("persistence", "Reload/restart the application after a successful action.", "The resulting state remains correct.")
    add("regression", "Re-run all previously passing cases for this requirement.", "No previously passing case regresses.")
    return steps


def _derive_gaps(
    ir: RequirementIR,
    units: list[ImplementationUnit],
    areas: list[TestArea],
) -> tuple[list[str], list[str]]:
    gaps: list[str] = []
    warnings: list[str] = []
    if not ir.input_rules and not ir.constraints:
        gaps.append("No explicit input contract was detected; validate the interface manually.")
    if not ir.output_rules and not ir.scenarios:
        gaps.append("No explicit observable output was detected; define an acceptance criterion manually.")
    if not ir.error_cases:
        warnings.append("No explicit error case was declared; hidden negative tests may still exist.")
    if not units:
        gaps.append("No implementation layer was inferred; review the requirement for missing behavioral clauses.")
    if not areas:
        gaps.append("No test area was inferred; add a scenario or acceptance condition.")
    return gaps, warnings


def decompose_requirement(
    source: RequirementIR | dict,
    constraints: ConstraintBoundaryReport | None = None,
) -> DecompositionPlan:
    """Return a generic requirement decomposition for one requirement."""
    ir, constraints = _coerce_inputs(source, constraints)
    interface_contract = _build_interface_contract(ir)
    units = _build_implementation_units(ir, constraints)
    areas = _build_test_areas(ir, constraints)
    verification_plan = _build_verification_plan(ir, constraints)
    gaps, warnings = _derive_gaps(ir, units, areas)
    return DecompositionPlan(
        id=ir.id,
        name=ir.name,
        interface_contract=interface_contract,
        implementation_units=units,
        test_areas=areas,
        verification_plan=verification_plan,
        gaps=gaps,
        warnings=warnings,
    )


def decomposition_to_dict(
    source: RequirementIR | dict,
    constraints: ConstraintBoundaryReport | None = None,
) -> dict[str, Any]:
    """Analyze a requirement and return a JSON-serializable dictionary."""
    return asdict(decompose_requirement(source, constraints))


def write_decomposition_analyses(
    output_dir: Path,
    nodes: list[dict],
    enabled: bool = True,
) -> list[str]:
    """Write shadow decomposition plans under ``.arc/analysis/<node>.decomposition.json``.

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
            plan = decompose_requirement(node)
        except (TypeError, ValueError):
            continue
        safe_id = re.sub(r"[^A-Za-z0-9._-]+", "_", str(node.get("id") or "unknown")).strip("._")
        if not safe_id:
            safe_id = "unknown"
        path = analysis_dir / f"{safe_id}.decomposition.json"
        path.write_text(
            json.dumps(asdict(plan), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        written.append(path.relative_to(output_dir).as_posix())
    return written
