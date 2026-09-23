"""Task-agnostic requirement analyzer.

This module turns one ARC-Bench requirement node (already parsed from YAML) into
a normalized ``RequirementIR``.  It is deliberately independent of any task:
it knows about requirement structure, scenario semantics, and generic software
engineering signals, but it contains no task names, field names, routes,
buttons, test inputs, or expected outputs.

The first version is deterministic and performs no I/O except for the optional
``write_requirement_analyses`` helper used by the shadow integration.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import re
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "1.0"
ANALYZER_NAME = "requirement-analyzer/1.0"


@dataclass
class ScenarioStep:
    keyword: str
    content: str


@dataclass
class Scenario:
    name: str
    steps: list[ScenarioStep] = field(default_factory=list)
    given: list[str] = field(default_factory=list)
    when: list[str] = field(default_factory=list)
    then: list[str] = field(default_factory=list)


@dataclass
class BoundaryRecord:
    kind: str
    raw: str
    source: str
    lower: int | None = None
    upper: int | None = None
    unit: str = ""


@dataclass
class RequirementIR:
    id: str
    name: str = ""
    type: str = ""
    description: str = ""
    scenarios: list[Scenario] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    states: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    input_rules: list[str] = field(default_factory=list)
    output_rules: list[str] = field(default_factory=list)
    error_cases: list[str] = field(default_factory=list)
    boundaries: list[BoundaryRecord] = field(default_factory=list)
    dependencies: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    schema_version: str = SCHEMA_VERSION
    analyzer: str = ANALYZER_NAME


_NORMATIVE_EN = (
    "must", "must not", "should", "should not", "shall", "shall not",
    "requires", "required", "prohibited", "is not allowed", "only",
)
_NORMATIVE_ZH = (
    "必须", "不得", "不能", "不可", "禁止", "应当", "需要", "只能",
    "确保", "须满足", "应满足", "应",
)

_INPUT_EN = (
    "input", "field", "form", "control", "label", "required", "optional",
    "length", "characters", "character", "format", "pattern", "valid",
    "invalid", "unique", "duplicate", "case-insensitive", "case insensitive",
    "case-sensitive", "case sensitive", "lowercase", "uppercase", "between",
    "range", "at least", "at most", "maximum", "minimum", "allowed",
    "selection", "option", "blank", "empty", "missing", "value", "type",
)
_INPUT_ZH = (
    "输入", "字段", "控件", "表单", "必填", "选填", "可选", "格式",
    "长度", "字符", "唯一", "重复", "忽略大小写", "大小写", "范围",
    "选项", "校验", "有效", "无效", "为空", "缺失", "类型", "至少",
    "至多", "不超过",
)

_OUTPUT_EN = (
    "display", "show", "visible", "return", "navigate", "redirect", "render",
    "appear", "respond", "response", "output", "page displays", "shows",
)
_OUTPUT_ZH = (
    "显示", "返回", "跳转", "导航", "呈现", "可见", "出现", "响应",
    "输出", "页面显示",
)

_ERROR_EN = (
    "error", "invalid", "missing", "fail", "failure", "reject", "deny",
    "incorrect", "unknown", "duplicate", "unauthorized", "forbidden",
    "must not", "should not", "does not", "cannot", "not create", "not show",
    "without",
)
_ERROR_ZH = (
    "错误", "失败", "无效", "缺失", "为空", "不一致", "重复", "不存在",
    "未知", "拒绝", "不得", "不能", "不会", "不显示", "不创建", "泄露",
    "禁止", "未", "无法",
)

_ENTITY_ROLE_EN = (
    ("user", r"\buser\b"),
    ("visitor", r"\bvisitor\b"),
    ("admin", r"\badmin\b"),
    ("administrator", r"\badministrator\b"),
    ("customer", r"\bcustomer\b"),
    ("owner", r"\bowner\b"),
)
_ENTITY_ROLE_ZH = (
    ("user", "用户"),
    ("visitor", "访客"),
    ("admin", "管理员"),
    ("customer", "客户"),
    ("owner", "所有者"),
)

_BOUNDARY_UNITS = (
    "characters", "character", "chars", "letters", "digits", "items",
    "records", "seconds", "minutes", "days", "attempts", "times", "entries",
    "字符", "个字符", "位", "天", "秒", "分钟", "次", "项", "条",
)


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _unique(values: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        value = _clean(value)
        if not value or value.lower() in seen:
            continue
        seen.add(value.lower())
        out.append(value)
    return out


def _sentences(text: str) -> list[str]:
    text = re.sub(r"\r\n?", "\n", text or "")
    text = re.sub(r"\n+", "\n", text)
    parts = re.split(r"(?<=[.!?。！？；;])\s*|\n+", text)
    return [p.strip() for p in parts if p.strip()]


def _scenario_text(scenarios: list[Scenario]) -> str:
    return "\n".join(
        [step.content for scenario in scenarios for step in scenario.steps]
    )


def _normalize_scenarios(node: dict) -> list[Scenario]:
    scenarios: list[Scenario] = []
    for item in node.get("scenarios") or []:
        if not isinstance(item, dict):
            continue
        scenario = Scenario(name=_clean(item.get("name")) or "unnamed scenario")
        current_section = "GIVEN"
        for step in item.get("steps") or []:
            if isinstance(step, dict):
                keyword = _clean(step.get("keyword")).upper()
                content = _clean(step.get("content"))
            else:
                keyword = "GIVEN"
                content = _clean(step)
            if not content:
                continue
            scenario.steps.append(ScenarioStep(keyword=keyword, content=content))
            if keyword == "GIVEN":
                current_section = "GIVEN"
                scenario.given.append(content)
            elif keyword == "WHEN":
                current_section = "WHEN"
                scenario.when.append(content)
            elif keyword == "THEN":
                current_section = "THEN"
                scenario.then.append(content)
            elif keyword == "AND":
                if current_section == "GIVEN":
                    scenario.given.append(content)
                elif current_section == "WHEN":
                    scenario.when.append(content)
                elif current_section == "THEN":
                    scenario.then.append(content)
        scenarios.append(scenario)
    return scenarios


def _has_any(text: str, phrases: tuple[str, ...]) -> bool:
    lower = text.lower()
    return any(phrase in lower for phrase in phrases)


def _extract_roles(text: str) -> list[str]:
    roles: list[str] = []
    for role, pattern in _ENTITY_ROLE_EN:
        if re.search(pattern, text, flags=re.IGNORECASE):
            roles.append(role)
    for role, phrase in _ENTITY_ROLE_ZH:
        if phrase in text:
            roles.append(role)
    if re.search(r"\b(system|application|app|website|server)\b", text, flags=re.IGNORECASE):
        roles.append("system")
    if re.search(r"(系统|应用|网站|服务端)", text):
        roles.append("system")
    return _unique(roles)


def _extract_entity_phrases(text: str) -> list[str]:
    phrases: list[str] = []
    en_pattern = re.compile(
        r"\b(?:system|application|app|website|server)\s+"
        r"(?:contains|maintains|stores|has|keeps|tracks)\s+"
        r"(?:a|an|the)?\s*(.{4,140}?)(?:[.;\n]|$)",
        flags=re.IGNORECASE,
    )
    for match in en_pattern.finditer(text):
        phrases.append(_clean(match.group(1)))
    zh_pattern = re.compile(
        r"(?:系统中存在|系统包含|系统提供|系统维护|系统保存|应用包含|页面提供)"
        r"([^。；;！？!?\n]{2,140})"
    )
    for match in zh_pattern.finditer(text):
        phrases.append(_clean(match.group(1)))
    return _unique(phrases)


def _extract_entities(text: str) -> list[str]:
    return _unique(_extract_roles(text) + _extract_entity_phrases(text))


def _extract_actions(scenarios: list[Scenario]) -> list[str]:
    actions = [clause for scenario in scenarios for clause in scenario.when]
    return _unique(actions)


def _extract_states(scenarios: list[Scenario]) -> list[str]:
    states: list[str] = []
    for scenario in scenarios:
        states.extend(scenario.given)
        states.extend(scenario.then)
    return _unique(states)


def _classify_sentences(sentences: list[str]) -> tuple[list[str], list[str], list[str], list[str]]:
    constraints: list[str] = []
    input_rules: list[str] = []
    output_rules: list[str] = []
    error_cases: list[str] = []
    for sentence in sentences:
        if _has_any(sentence, _NORMATIVE_EN) or _has_any(sentence, _NORMATIVE_ZH):
            constraints.append(sentence)
        if _has_any(sentence, _INPUT_EN) or _has_any(sentence, _INPUT_ZH):
            input_rules.append(sentence)
        if _has_any(sentence, _OUTPUT_EN) or _has_any(sentence, _OUTPUT_ZH):
            output_rules.append(sentence)
        if _has_any(sentence, _ERROR_EN) or _has_any(sentence, _ERROR_ZH):
            error_cases.append(sentence)
    return constraints, input_rules, output_rules, error_cases


def _sentence_source(sentence: str, description: str) -> str:
    return "description" if sentence in _sentences(description) else "scenario"


def _boundary_record(
    sentence: str,
    kind: str,
    lower: int | None,
    upper: int | None,
    unit: str = "",
    source: str = "requirement",
) -> BoundaryRecord:
    return BoundaryRecord(
        kind=kind,
        raw=_clean(sentence),
        source=source,
        lower=lower,
        upper=upper,
        unit=unit,
    )


def _extract_boundaries(sentences: list[str], description: str) -> list[BoundaryRecord]:
    records: list[BoundaryRecord] = []
    seen: set[tuple] = set()

    range_re = re.compile(
        r"(?P<a>\d+)\s*(?:-|–|—|~|～|到|至|to|through)\s*(?P<b>\d+)",
        flags=re.IGNORECASE,
    )
    between_re = re.compile(
        r"between\s+(?P<a>\d+)\s+(?:and|to)\s+(?P<b>\d+)",
        flags=re.IGNORECASE,
    )
    min_re = re.compile(
        r"(?:at least|minimum(?: of)?|不少于|至少|最低|不少于)\s*(?P<n>\d+)",
        flags=re.IGNORECASE,
    )
    max_re = re.compile(
        r"(?:at most|not more than|maximum(?: of)?|不超过|至多|最多)\s*(?P<n>\d+)",
        flags=re.IGNORECASE,
    )
    exact_re = re.compile(
        r"(?P<n>\d+)\s*(?:个|位)?\s*(?:"
        + "|".join(re.escape(unit) for unit in _BOUNDARY_UNITS)
        + r")",
        flags=re.IGNORECASE,
    )

    for sentence in sentences:
        source = _sentence_source(sentence, description)
        for match in range_re.finditer(sentence):
            lower, upper = int(match.group("a")), int(match.group("b"))
            if lower > upper:
                lower, upper = upper, lower
            record = _boundary_record(sentence, "range", lower, upper, source=source)
            key = (record.kind, record.lower, record.upper, record.unit)
            if key not in seen:
                seen.add(key)
                records.append(record)
        for match in between_re.finditer(sentence):
            record = _boundary_record(
                sentence,
                "range",
                int(match.group("a")),
                int(match.group("b")),
                source=source,
            )
            key = (record.kind, record.lower, record.upper, record.unit)
            if key not in seen:
                seen.add(key)
                records.append(record)
        for match in min_re.finditer(sentence):
            record = _boundary_record(sentence, "minimum", int(match.group("n")), None, source=source)
            key = (record.kind, record.lower, record.upper, record.unit)
            if key not in seen:
                seen.add(key)
                records.append(record)
        for match in max_re.finditer(sentence):
            record = _boundary_record(sentence, "maximum", int(match.group("n")), None, source=source)
            key = (record.kind, record.lower, record.upper, record.unit)
            if key not in seen:
                seen.add(key)
                records.append(record)
        for match in exact_re.finditer(sentence):
            value = int(match.group("n"))
            record = _boundary_record(sentence, "exact", value, value, source=source)
            key = (record.kind, record.lower, record.upper, record.unit)
            if key not in seen:
                seen.add(key)
                records.append(record)
    return records


def _derive_assumptions_and_warnings(
    node: dict,
    scenarios: list[Scenario],
    dependencies: list[str],
    sentences: list[str],
    boundaries: list[BoundaryRecord],
    input_rules: list[str],
    error_cases: list[str],
) -> tuple[list[str], list[str]]:
    assumptions: list[str] = []
    warnings: list[str] = []
    if not dependencies:
        assumptions.append("No dependencies declared; treat this node as standalone unless ordering is external.")
    if not scenarios:
        assumptions.append("No scenarios declared; analysis is inferred from the free-text description only.")
    for scenario in scenarios:
        if not scenario.given:
            warnings.append(f"Scenario '{scenario.name}' has no explicit GIVEN precondition.")
        if not scenario.when and not scenario.then:
            warnings.append(f"Scenario '{scenario.name}' has no WHEN or THEN step.")
        elif not scenario.then:
            warnings.append(f"Scenario '{scenario.name}' has no observable THEN outcome.")
    if sentences and not boundaries and not input_rules and not error_cases:
        warnings.append(
            "No explicit boundary, input-rule, or error-case clause was detected; "
            "verify hidden boundary and failure cases separately."
        )
    return assumptions, warnings


def analyze_requirement(node: dict) -> RequirementIR:
    """Return a task-agnostic requirement IR for one requirement node."""
    if not isinstance(node, dict):
        raise TypeError("requirement node must be a dict")
    node_id = _clean(node.get("id"))
    if not node_id:
        raise ValueError("requirement node is missing an id")

    name = _clean(node.get("name"))
    node_type = _clean(node.get("type"))
    description = _clean(node.get("description"))
    scenarios = _normalize_scenarios(node)
    dependencies = [str(dep) for dep in (node.get("dependencies") or []) if str(dep).strip()]

    text = "\n".join([description, _scenario_text(scenarios)])
    sentences = _sentences(text)
    constraints, input_rules, output_rules, error_cases = _classify_sentences(sentences)
    boundaries = _extract_boundaries(sentences, description)
    assumptions, warnings = _derive_assumptions_and_warnings(
        node,
        scenarios,
        dependencies,
        sentences,
        boundaries,
        input_rules,
        error_cases,
    )

    return RequirementIR(
        id=node_id,
        name=name,
        type=node_type,
        description=description,
        scenarios=scenarios,
        entities=_extract_entities(text),
        states=_extract_states(scenarios),
        actions=_extract_actions(scenarios),
        constraints=_unique(constraints),
        input_rules=_unique(input_rules),
        output_rules=_unique(output_rules),
        error_cases=_unique(error_cases),
        boundaries=boundaries,
        dependencies=dependencies,
        assumptions=assumptions,
        warnings=warnings,
    )


def requirement_analysis_to_dict(node: dict) -> dict[str, Any]:
    """Analyze a node and return a JSON-serializable dictionary."""
    return asdict(analyze_requirement(node))


def write_requirement_analyses(
    output_dir: Path,
    nodes: list[dict],
    enabled: bool = True,
) -> list[str]:
    """Write shadow analysis files under ``.arc/analysis/<node-id>.json``.

    The files are intentionally observational only; callers must not feed them
    back into prompts until a later module integrates them explicitly.
    """
    if not enabled:
        return []
    analysis_dir = output_dir / ".arc" / "analysis"
    analysis_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for node in nodes:
        try:
            ir = analyze_requirement(node)
        except (TypeError, ValueError):
            continue
        safe_id = re.sub(r"[^A-Za-z0-9._-]+", "_", str(node.get("id") or "unknown")).strip("._")
        if not safe_id:
            safe_id = "unknown"
        path = analysis_dir / f"{safe_id}.json"
        path.write_text(
            json.dumps(asdict(ir), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        written.append(path.relative_to(output_dir).as_posix())
    return written
