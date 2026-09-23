"""Task-agnostic constraint and boundary analyzer.

This module consumes a ``RequirementIR`` produced by
``requirement_analyzer`` (or a raw requirement node) and emits a generic
test-matrix skeleton.  It knows about software-engineering boundary categories,
not about any particular business domain, field, route, or test value.

The module performs no I/O except for the optional
``write_constraint_analyses`` helper used by the shadow integration.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import re
from pathlib import Path
from typing import Any

from requirement_analyzer import (
    RequirementIR,
    analyze_requirement,
)


SCHEMA_VERSION = "1.0"
ANALYZER_NAME = "constraint-boundary-analyzer/1.0"


@dataclass
class ConstraintRule:
    rule_id: str
    category: str
    statement: str
    source: str = "requirement"
    lower: int | None = None
    upper: int | None = None
    unit: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass
class BoundaryCase:
    case_id: str
    category: str
    scenario_hint: str
    source: str = "requirement"
    priority: str = "required"


@dataclass
class ConstraintBoundaryReport:
    id: str
    name: str = ""
    rules: list[ConstraintRule] = field(default_factory=list)
    cases: list[BoundaryCase] = field(default_factory=list)
    coverage_gaps: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    schema_version: str = SCHEMA_VERSION
    analyzer: str = ANALYZER_NAME


_REQUIRED_EN = (
    "required", "mandatory", "must be provided", "must not be empty",
    "must be present", "is required",
)
_REQUIRED_ZH = ("必填", "必须填写", "必须提供", "不得为空", "必须存在")

_OPTIONAL_EN = ("optional", "may be omitted", "not required")
_OPTIONAL_ZH = ("选填", "可选", "可不填")

_UNIQUE_EN = ("unique", "duplicate", "already exists", "must not repeat")
_UNIQUE_ZH = ("唯一", "重复", "已存在", "不得重复")

_CASE_EN = (
    "case-insensitive", "case insensitive", "case-sensitive", "case sensitive",
    "lowercase", "uppercase",
)
_CASE_ZH = ("忽略大小写", "大小写", "区分大小写", "大写", "小写")

_WHITESPACE_EN = ("whitespace", "leading", "trailing", "trim", "space")
_WHITESPACE_ZH = ("空白", "首尾空格", "去除首尾空格", "空格", "去空格")

_FORMAT_EN = (
    "format", "pattern", "characters", "length", "digits", "letters",
    "must match", "allowed characters",
)
_FORMAT_ZH = ("格式", "字符", "长度", "位数", "只能包含", "允许字符", "匹配")

_PERSISTENCE_EN = ("persist", "refresh", "reload", "restart", "durable", "storage")
_PERSISTENCE_ZH = ("持久化", "刷新", "重新加载", "重启", "保存", "恢复")


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _has_any(text: str, phrases: tuple[str, ...]) -> bool:
    lower = text.lower()
    return any(phrase in lower for phrase in phrases)


def _coerce_ir(source: RequirementIR | dict) -> RequirementIR:
    if isinstance(source, RequirementIR):
        return source
    if isinstance(source, dict):
        return analyze_requirement(source)
    raise TypeError("source must be a RequirementIR or requirement node dict")


def _input_signal_categories(sentence: str) -> list[str]:
    categories: list[str] = []
    if _has_any(sentence, _REQUIRED_EN) or _has_any(sentence, _REQUIRED_ZH):
        categories.append("required")
    if _has_any(sentence, _OPTIONAL_EN) or _has_any(sentence, _OPTIONAL_ZH):
        categories.append("optional")
    if _has_any(sentence, _UNIQUE_EN) or _has_any(sentence, _UNIQUE_ZH):
        categories.append("unique")
    if _has_any(sentence, _CASE_EN) or _has_any(sentence, _CASE_ZH):
        categories.append("case")
    if _has_any(sentence, _WHITESPACE_EN) or _has_any(sentence, _WHITESPACE_ZH):
        categories.append("whitespace")
    if _has_any(sentence, _FORMAT_EN) or _has_any(sentence, _FORMAT_ZH):
        categories.append("format")
    return categories


def _build_rules(ir: RequirementIR) -> list[ConstraintRule]:
    rules: list[ConstraintRule] = []
    index = 0

    for boundary in ir.boundaries:
        index += 1
        rules.append(
            ConstraintRule(
                rule_id=f"constraint-{index}",
                category="numeric",
                statement=boundary.raw,
                source=boundary.source,
                lower=boundary.lower,
                upper=boundary.upper,
                unit=boundary.unit,
            )
        )

    for sentence in ir.input_rules:
        categories = _input_signal_categories(sentence)
        for category in categories:
            index += 1
            rules.append(
                ConstraintRule(
                    rule_id=f"constraint-{index}",
                    category=category,
                    statement=sentence,
                    source="requirement",
                )
            )

    for sentence in ir.error_cases:
        index += 1
        rules.append(
            ConstraintRule(
                rule_id=f"constraint-{index}",
                category="error",
                statement=sentence,
                source="requirement",
            )
        )
    return rules


def _append_case(
    cases: list[BoundaryCase],
    seen: set[tuple[str, str]],
    category: str,
    scenario_hint: str,
    source: str = "requirement",
    priority: str = "required",
) -> None:
    key = (category, _clean(scenario_hint).lower())
    if key in seen:
        return
    seen.add(key)
    cases.append(
        BoundaryCase(
            case_id=f"case-{len(cases) + 1}",
            category=category,
            scenario_hint=_clean(scenario_hint),
            source=source,
            priority=priority,
        )
    )


def _generate_cases(ir: RequirementIR, rules: list[ConstraintRule]) -> list[BoundaryCase]:
    cases: list[BoundaryCase] = []
    seen: set[tuple[str, str]] = set()

    if ir.scenarios:
        for scenario_index, scenario in enumerate(ir.scenarios, 1):
            _append_case(
                cases,
                seen,
                "happy_path",
                f"Follow scenario '{scenario.name}': preconditions -> action -> observable outcome.",
                source="scenario",
                priority="required",
            )
    else:
        _append_case(
            cases,
            seen,
            "happy_path",
            "Execute the primary action described by the requirement and verify the expected visible outcome.",
            source="description",
            priority="required",
        )

    categories = {rule.category for rule in rules}

    if "required" in categories:
        _append_case(cases, seen, "missing", "Omit every required input and verify a visible error with no side effect.")
        _append_case(cases, seen, "provided", "Provide every required input and verify the happy path succeeds.")
    if "optional" in categories:
        _append_case(cases, seen, "optional_omitted", "Omit the optional input and verify the action still succeeds.")
        _append_case(cases, seen, "optional_provided", "Provide the optional input and verify it is accepted or normalized correctly.")
    if "unique" in categories:
        _append_case(cases, seen, "unique", "Provide a value that has not been used before and verify it is accepted.")
        _append_case(cases, seen, "duplicate", "Repeat an already-used value and verify the duplicate is rejected without side effects.")
    if "case" in categories:
        _append_case(cases, seen, "case_equivalent", "Verify equivalent values with different casing are treated consistently with the stated rule.")
        _append_case(cases, seen, "case_distinct", "Verify values whose case must be distinguished are not collapsed incorrectly.")
    if "whitespace" in categories:
        _append_case(cases, seen, "whitespace_normalized", "Provide a value with leading/trailing whitespace and verify normalization or rejection as specified.")
        _append_case(cases, seen, "whitespace_only", "Provide whitespace-only input and verify the expected error or normalization.")
    if "format" in categories:
        _append_case(cases, seen, "valid_format", "Provide a value matching the stated format and verify acceptance.")
        _append_case(cases, seen, "invalid_format", "Provide a value that violates the stated format and verify a visible error with no side effect.")

    for rule in rules:
        if rule.category != "numeric":
            continue
        lower = rule.lower
        upper = rule.upper
        if lower is not None and upper is not None and lower != upper:
            _append_case(cases, seen, "within_range", f"Use the boundary described by: {rule.statement}")
            _append_case(cases, seen, "lower_boundary", f"Test the lower accepted value from: {rule.statement}")
            _append_case(cases, seen, "upper_boundary", f"Test the upper accepted value from: {rule.statement}")
            _append_case(cases, seen, "below_lower", f"Test the value just below the lower accepted boundary from: {rule.statement}")
            _append_case(cases, seen, "above_upper", f"Test the value just above the upper accepted boundary from: {rule.statement}")
            _append_case(cases, seen, "non_numeric", "Provide a non-numeric or incompatible type and verify rejection or coercion per the requirement.")
            _append_case(cases, seen, "empty", "Provide an empty/missing value and verify the required error or default behavior.")
        elif lower is not None and upper is not None and lower == upper:
            _append_case(cases, seen, "exact", f"Test the exact accepted value from: {rule.statement}")
            _append_case(cases, seen, "below_exact", f"Test the value below the exact accepted boundary from: {rule.statement}")
            _append_case(cases, seen, "above_exact", f"Test the value above the exact accepted boundary from: {rule.statement}")
            _append_case(cases, seen, "non_numeric", "Provide a non-numeric or incompatible type and verify rejection or coercion per the requirement.")
            _append_case(cases, seen, "empty", "Provide an empty/missing value and verify the required error or default behavior.")
        elif lower is not None:
            _append_case(cases, seen, "minimum", f"Test the minimum accepted value from: {rule.statement}")
            _append_case(cases, seen, "below_minimum", f"Test the value just below the minimum from: {rule.statement}")
            _append_case(cases, seen, "non_numeric", "Provide a non-numeric or incompatible type and verify rejection or coercion per the requirement.")
            _append_case(cases, seen, "empty", "Provide an empty/missing value and verify the required error or default behavior.")
        elif upper is not None:
            _append_case(cases, seen, "maximum", f"Test the maximum accepted value from: {rule.statement}")
            _append_case(cases, seen, "above_maximum", f"Test the value just above the maximum from: {rule.statement}")
            _append_case(cases, seen, "non_numeric", "Provide a non-numeric or incompatible type and verify rejection or coercion per the requirement.")
            _append_case(cases, seen, "empty", "Provide an empty/missing value and verify the required error or default behavior.")

    if "error" in categories:
        _append_case(
            cases,
            seen,
            "expected_error",
            "For each declared failure condition, verify a visible error and that no entity/state is created or changed.",
            priority="required",
        )

    text = "\n".join(
        [ir.description] + [step.content for scenario in ir.scenarios for step in scenario.steps]
    )
    if ir.dependencies:
        _append_case(cases, seen, "dependency_met", "Run the action after all declared dependencies are satisfied.")
        _append_case(cases, seen, "dependency_missing", "Run the action before a declared dependency is satisfied and verify the expected rejection or fallback.")
    if _has_any(text, _PERSISTENCE_EN) or _has_any(text, _PERSISTENCE_ZH):
        _append_case(
            cases,
            seen,
            "persistence_reload",
            "After a successful action, reload/restart the application and verify the resulting state remains correct.",
            priority="recommended",
        )

    return cases


def _derive_gaps(ir: RequirementIR, rules: list[ConstraintRule]) -> tuple[list[str], list[str]]:
    gaps: list[str] = []
    warnings: list[str] = []
    categories = {rule.category for rule in rules}
    if not ir.input_rules:
        gaps.append("No input rule was detected; manually enumerate missing, empty, format, type, duplicate, and state cases.")
    if not ir.boundaries:
        gaps.append("No numeric/length boundary was detected; manually probe minimum, maximum, and off-by-one values.")
    if not ir.error_cases:
        gaps.append("No negative/error clause was detected; add invalid-input and state-conflict scenarios.")
    if ir.dependencies and "state" not in categories:
        warnings.append("Dependency edges were declared; verify dependent-state preconditions explicitly.")
    return gaps, warnings


def analyze_constraint_boundaries(source: RequirementIR | dict) -> ConstraintBoundaryReport:
    """Return a generic constraint/boundary matrix for one requirement."""
    ir = _coerce_ir(source)
    rules = _build_rules(ir)
    cases = _generate_cases(ir, rules)
    gaps, warnings = _derive_gaps(ir, rules)
    return ConstraintBoundaryReport(
        id=ir.id,
        name=ir.name,
        rules=rules,
        cases=cases,
        coverage_gaps=gaps,
        warnings=warnings,
    )


def constraint_boundary_analysis_to_dict(source: RequirementIR | dict) -> dict[str, Any]:
    """Analyze a requirement and return a JSON-serializable dictionary."""
    return asdict(analyze_constraint_boundaries(source))


def write_constraint_analyses(
    output_dir: Path,
    nodes: list[dict],
    enabled: bool = True,
) -> list[str]:
    """Write shadow constraint reports under ``.arc/analysis/<node>.constraints.json``.

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
            report = analyze_constraint_boundaries(node)
        except (TypeError, ValueError):
            continue
        safe_id = re.sub(r"[^A-Za-z0-9._-]+", "_", str(node.get("id") or "unknown")).strip("._")
        if not safe_id:
            safe_id = "unknown"
        path = analysis_dir / f"{safe_id}.constraints.json"
        path.write_text(
            json.dumps(asdict(report), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        written.append(path.relative_to(output_dir).as_posix())
    return written
