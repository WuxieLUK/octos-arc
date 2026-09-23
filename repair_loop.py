"""Task-agnostic repair loop.

This module provides a small, framework-independent repair loop.  It does not
know how to edit code or run a specific test framework; callers inject those
actions.  The loop enforces a generic discipline:

    test -> analyze -> reason -> modify -> retest -> regression test

The optional ``write_repair_loop_manifests`` helper is shadow-only and does not
execute a repair.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import re
from pathlib import Path
from typing import Any, Callable

from failure_analyzer import (
    FailureAnalysisReport,
    analyze_failure,
)
from requirement_analyzer import RequirementIR, analyze_requirement
from test_runner import TestRunResult


SCHEMA_VERSION = "1.0"
ANALYZER_NAME = "repair-loop/1.0"


@dataclass
class RepairLoopConfig:
    max_rounds: int = 5
    stop_after_stable_failures: int = 2
    require_regression: bool = True


@dataclass
class RepairRound:
    index: int
    before: TestRunResult
    analysis: FailureAnalysisReport
    action: str
    applied: bool
    after: TestRunResult
    regression: TestRunResult | None = None
    made_progress: bool = False


@dataclass
class RepairLoopResult:
    id: str
    passed: bool
    rounds: list[RepairRound] = field(default_factory=list)
    final_result: TestRunResult | None = None
    stop_reason: str = ""
    failure_signature: str = ""
    warnings: list[str] = field(default_factory=list)
    schema_version: str = SCHEMA_VERSION
    analyzer: str = ANALYZER_NAME


@dataclass
class RepairLoopManifest:
    id: str
    name: str = ""
    status: str = "shadow"
    default_config: dict[str, Any] = field(default_factory=dict)
    stop_conditions: list[str] = field(default_factory=list)
    notes: str = "Repair-loop orchestration is available through run_repair_loop() and optional prompt/control injection."
    schema_version: str = SCHEMA_VERSION
    analyzer: str = ANALYZER_NAME


def _coerce_ir(source: RequirementIR | dict | None) -> RequirementIR | None:
    if source is None:
        return None
    if isinstance(source, RequirementIR):
        return source
    if isinstance(source, dict):
        return analyze_requirement(source)
    raise TypeError("source must be a RequirementIR, requirement node dict, or None")


def _same_failure(left: TestRunResult, right: TestRunResult) -> bool:
    return (
        left.passed == right.passed
        and left.error_signature == right.error_signature
        and left.output_digest == right.output_digest
    )


def _made_progress(before: TestRunResult, after: TestRunResult) -> bool:
    return after.passed or not _same_failure(before, after)


def format_repair_loop_policy(config: RepairLoopConfig | None = None) -> str:
    """Render the generic repair-loop discipline for a model prompt."""
    cfg = config or RepairLoopConfig()
    return (
        "[repair loop policy]\n"
        "Before editing, classify the failure and identify whether it belongs to implementation logic, "
        "requirement-to-interface mapping, boundary/invalid-input handling, state/persistence, UI/selector rendering, "
        "or build/runtime configuration.\n"
        f"Make one focused root-cause change, then verify it before making another change. "
        f"Stop if the same failure signature repeats {cfg.stop_after_stable_failures} times without progress. "
        f"After the targeted tests pass, rerun previously passing related behavior to check for regressions.\n"
        f"Maximum repair rounds for this loop: {cfg.max_rounds}.\n"
    )


def run_repair_loop(
    source: RequirementIR | dict | None,
    run_tests: Callable[[], TestRunResult],
    reason: Callable[[FailureAnalysisReport, int], str],
    apply_repair: Callable[[str, int], bool],
    run_regression: Callable[[], TestRunResult] | None = None,
    config: RepairLoopConfig | None = None,
) -> RepairLoopResult:
    """Run a bounded repair loop with caller-supplied test/reason/apply actions."""
    ir = _coerce_ir(source)
    cfg = config or RepairLoopConfig()
    report_id = str(ir.id) if ir is not None else "run"
    rounds: list[RepairRound] = []
    warnings: list[str] = []

    current = run_tests()
    if current.passed:
        return RepairLoopResult(
            id=report_id,
            passed=True,
            rounds=rounds,
            final_result=current,
            stop_reason="already_passing",
            failure_signature=current.error_signature,
            warnings=warnings,
        )

    stable_failures = 0
    for index in range(1, cfg.max_rounds + 1):
        analysis = analyze_failure(current, ir)
        action = reason(analysis, index)
        applied = apply_repair(action, index)
        after = run_tests()
        regression: TestRunResult | None = None

        if after.passed:
            if cfg.require_regression and run_regression is not None:
                regression = run_regression()
                if not regression.passed:
                    rounds.append(
                        RepairRound(
                            index=index,
                            before=current,
                            analysis=analysis,
                            action=action,
                            applied=applied,
                            after=after,
                            regression=regression,
                            made_progress=True,
                        )
                    )
                    return RepairLoopResult(
                        id=report_id,
                        passed=False,
                        rounds=rounds,
                        final_result=after,
                        stop_reason="regression_failed",
                        failure_signature=regression.error_signature,
                        warnings=warnings + ["The targeted test passed, but the regression run failed."],
                    )
            rounds.append(
                RepairRound(
                    index=index,
                    before=current,
                    analysis=analysis,
                    action=action,
                    applied=applied,
                    after=after,
                    regression=regression,
                    made_progress=True,
                )
            )
            return RepairLoopResult(
                id=report_id,
                passed=True,
                rounds=rounds,
                final_result=after,
                stop_reason="success",
                failure_signature=after.error_signature,
                warnings=warnings,
            )

        progressed = _made_progress(current, after)
        if _same_failure(current, after):
            stable_failures += 1
        else:
            stable_failures = 0
        rounds.append(
            RepairRound(
                index=index,
                before=current,
                analysis=analysis,
                action=action,
                applied=applied,
                after=after,
                regression=None,
                made_progress=progressed,
            )
        )
        if stable_failures >= cfg.stop_after_stable_failures:
            return RepairLoopResult(
                id=report_id,
                passed=False,
                rounds=rounds,
                final_result=after,
                stop_reason="no_progress",
                failure_signature=after.error_signature,
                warnings=warnings + [f"The same failure signature repeated {stable_failures} times."],
            )
        current = after

    return RepairLoopResult(
        id=report_id,
        passed=False,
        rounds=rounds,
        final_result=current,
        stop_reason="max_rounds",
        failure_signature=current.error_signature,
        warnings=warnings,
    )


def build_repair_loop_manifest(source: RequirementIR | dict) -> RepairLoopManifest:
    ir = source if isinstance(source, RequirementIR) else analyze_requirement(source)
    config = RepairLoopConfig()
    return RepairLoopManifest(
        id=ir.id,
        name=ir.name,
        default_config=asdict(config),
        stop_conditions=["already_passing", "success", "regression_failed", "no_progress", "max_rounds"],
    )


def write_repair_loop_manifests(
    output_dir: Path,
    nodes: list[dict],
    enabled: bool = True,
) -> list[str]:
    """Write shadow repair-loop manifests under ``.arc/analysis/<node>.repair-loop.json``."""
    if not enabled:
        return []
    analysis_dir = output_dir / ".arc" / "analysis"
    analysis_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for node in nodes:
        try:
            manifest = build_repair_loop_manifest(node)
        except (TypeError, ValueError):
            continue
        safe_id = re.sub(r"[^A-Za-z0-9._-]+", "_", str(node.get("id") or "unknown")).strip("._")
        if not safe_id:
            safe_id = "unknown"
        path = analysis_dir / f"{safe_id}.repair-loop.json"
        path.write_text(
            json.dumps(asdict(manifest), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        written.append(path.relative_to(output_dir).as_posix())
    return written
