"""Task-agnostic test failure analyzer.

This module turns a normalized ``TestRunResult`` into root-cause hypotheses and
generic next actions.  It intentionally does not propose task-specific patches
or hardcode field names, routes, buttons, or expected values.

The shadow helper ``write_failure_analyzer_manifests`` only describes the
analyzer's capabilities; it does not consume prompts or execute commands.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import re
from pathlib import Path
from typing import Any

from requirement_analyzer import RequirementIR, analyze_requirement
from test_runner import TestRunResult


SCHEMA_VERSION = "1.0"
ANALYZER_NAME = "failure-analyzer/1.0"


@dataclass
class FailureHypothesis:
    category: str
    confidence: str
    hypothesis: str
    evidence: str
    suggested_action: str


@dataclass
class FailureAnalysisReport:
    id: str
    classification: str
    failure_signature: str
    hypotheses: list[FailureHypothesis] = field(default_factory=list)
    next_actions: list[str] = field(default_factory=list)
    evidence: str = ""
    warnings: list[str] = field(default_factory=list)
    schema_version: str = SCHEMA_VERSION
    analyzer: str = ANALYZER_NAME


@dataclass
class FailureAnalyzerManifest:
    id: str
    name: str = ""
    status: str = "shadow"
    supported_classifications: list[str] = field(default_factory=list)
    notes: str = "Failure analysis is available through analyze_failure() and is not injected into prompts yet."
    schema_version: str = SCHEMA_VERSION
    analyzer: str = ANALYZER_NAME


_CLASSIFICATION_SIGNALS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("build_or_install", ("npm", "pnpm", "yarn", "build", "install", "syntaxerror", "cannot find module", "command not found", "error ts")),
    ("startup", ("eaddrinuse", "address already in use", "listen eacces", "cannot start", "early exit", "exited early", "端口已占用", "启动失败")),
    ("health_or_endpoint", ("404", "connection refused", "connection reset", "econnrefused", "econnreset", "health", "unreachable", "无法访问", "连接被拒绝")),
    ("locator_or_visibility", ("locator", "getbyrole", "getbylabel", "tobevisible", "click", "fill", "element", "visible", "等待元素", "不可见")),
    ("assertion_mismatch", ("expect", "assert", "expected", "received", "tocontain", "toequal", "期望", "实际")),
    ("data_integrity", ("duplicate", "unique", "integrity", "persist", "重复", "唯一", "完整性", "持久化")),
    ("authorization", ("authorize", "authorized", "authorization", "permission", "forbidden", "unauthorized", "授权", "权限", "未授权")),
    ("timeout", ("timed out", "timeout", "timedout")),
)

_SUPPORTED_CLASSIFICATIONS = [item[0] for item in _CLASSIFICATION_SIGNALS] + ["unknown", "no_failure"]


def _clean(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _combined_output(result: TestRunResult) -> str:
    return f"{result.stderr_tail}\n{result.stdout_tail}\n{result.error_signature}".lower()


def _classify(result: TestRunResult) -> str:
    if result.passed:
        return "no_failure"
    output = _combined_output(result)
    for category, signals in _CLASSIFICATION_SIGNALS:
        if any(signal in output for signal in signals):
            return category
    if result.return_code not in (None, 0):
        return "command_failed"
    return "unknown"


def _signature(result: TestRunResult) -> str:
    if result.passed:
        return "ok"
    return re.sub(r"\s+", " ", result.error_signature)[:300]


def _hypotheses_for(classification: str, result: TestRunResult, ir: RequirementIR | None) -> list[FailureHypothesis]:
    output = _combined_output(result)
    hypotheses: list[FailureHypothesis] = []

    def add(category: str, confidence: str, hypothesis: str, evidence: str, action: str) -> None:
        hypotheses.append(FailureHypothesis(category, confidence, hypothesis, evidence, action))

    if classification == "timeout":
        add("timeout", "medium", "The required observable outcome did not become available within the timeout.", result.error_signature, "Inspect the UI/service boundary and state transition; determine whether the element, endpoint, or state is missing, hidden, or slow.")
    elif classification == "build_or_install":
        add("build", "high", "The build or install step failed before behavior could be exercised.", result.error_signature, "Inspect the changed source/manifest for syntax, import, or dependency errors and rerun the build.")
    elif classification == "startup":
        add("startup", "high", "The service could not bind, start, or remain alive.", result.error_signature, "Inspect the service entry point, port configuration, and startup ordering; rerun startup before testing behavior.")
    elif classification == "health_or_endpoint":
        add("endpoint", "high", "The required request/health boundary is missing or unreachable.", result.error_signature, "Trace the request from the entry point to the handler and verify the required success response is produced.")
    elif classification == "locator_or_visibility":
        add("ui_contract", "medium", "The expected user-facing element, accessible name, or visibility state does not match the rendered application.", result.error_signature, "Compare the rendered user interface with the requirement's observable contract; fix the missing/hidden control or its accessible name.")
    elif classification == "assertion_mismatch":
        add("behavior_mismatch", "medium", "The implementation's output/state differs from the expected acceptance outcome.", result.error_signature, "Trace requirement -> interface -> implementation and adjust the behavior, not the test expectation.")
    elif classification == "data_integrity":
        add("data", "medium", "A data integrity or persistence constraint is not being enforced.", result.error_signature, "Inspect the data/validation boundary and verify uniqueness, persistence, or integrity rules are applied before state mutation.")
    elif classification == "authorization":
        add("authorization", "medium", "A permission or role boundary is not being enforced.", result.error_signature, "Inspect the authorization boundary and verify unauthorized actions are rejected without side effects.")
    elif classification == "command_failed":
        add("command", "high", "The supplied test command exited non-zero without a recognized failure signal.", result.error_signature, "Read the full captured output, identify the first error line, and trace it back to a build/startup/runtime layer.")
    else:
        add("unknown", "low", "The failure signature is not recognized; inspect the raw output directly.", result.error_signature, "Collect full logs and map the first observed error to the requirement or implementation layer.")

    if ir is not None and ir.error_cases:
        add("requirement_gap", "low", "The requirement declares error cases that may not have a corresponding implementation path.", _clean("\n".join(ir.error_cases))[:300], "Verify each declared error case is implemented and tested.")
    return hypotheses


def _next_actions(classification: str) -> list[str]:
    actions = [
        "Re-run the same command and preserve the full output for comparison.",
        "Inspect the diff or changed source files and map the first error to a requirement/interface/implementation layer.",
        "Before patching, state the expected behavior and the observed behavior explicitly.",
        "After a fix, add or rerun a focused regression case that would have caught this failure.",
    ]
    if classification == "no_failure":
        return ["No failure detected; no repair action is required."]
    return actions


def analyze_failure(result: TestRunResult, source: RequirementIR | dict | None = None) -> FailureAnalysisReport:
    """Analyze one normalized test result and return generic root-cause leads."""
    if not isinstance(result, TestRunResult):
        raise TypeError("result must be a TestRunResult")
    ir: RequirementIR | None
    if source is None:
        ir = None
    elif isinstance(source, RequirementIR):
        ir = source
    elif isinstance(source, dict):
        ir = analyze_requirement(source)
    else:
        raise TypeError("source must be a RequirementIR, requirement node dict, or None")

    classification = _classify(result)
    report_id = str(ir.id) if ir is not None else "run"
    return FailureAnalysisReport(
        id=report_id,
        classification=classification,
        failure_signature=_signature(result),
        hypotheses=_hypotheses_for(classification, result, ir),
        next_actions=_next_actions(classification),
        evidence=_clean(result.error_signature),
        warnings=[],
    )


def format_failure_analysis(report: FailureAnalysisReport) -> str:
    """Render a compact, generic failure-analysis block for prompts."""
    lines = [f"Classification: {report.classification}"]
    for hypothesis in report.hypotheses[:3]:
        lines.append(f"- {hypothesis.category}: {hypothesis.hypothesis} -> {hypothesis.suggested_action}")
    actions = report.next_actions[:2]
    if actions:
        lines.append("Next actions: " + "; ".join(actions))
    return "\n".join(lines)


def build_failure_analyzer_manifest(source: RequirementIR | dict) -> FailureAnalyzerManifest:
    ir = source if isinstance(source, RequirementIR) else analyze_requirement(source)
    return FailureAnalyzerManifest(
        id=ir.id,
        name=ir.name,
        supported_classifications=_SUPPORTED_CLASSIFICATIONS,
    )


def write_failure_analyzer_manifests(
    output_dir: Path,
    nodes: list[dict],
    enabled: bool = True,
) -> list[str]:
    """Write shadow analyzer manifests under ``.arc/analysis/<node>.failure-analyzer.json``."""
    if not enabled:
        return []
    analysis_dir = output_dir / ".arc" / "analysis"
    analysis_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for node in nodes:
        try:
            manifest = build_failure_analyzer_manifest(node)
        except (TypeError, ValueError):
            continue
        safe_id = re.sub(r"[^A-Za-z0-9._-]+", "_", str(node.get("id") or "unknown")).strip("._")
        if not safe_id:
            safe_id = "unknown"
        path = analysis_dir / f"{safe_id}.failure-analyzer.json"
        path.write_text(
            json.dumps(asdict(manifest), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        written.append(path.relative_to(output_dir).as_posix())
    return written
