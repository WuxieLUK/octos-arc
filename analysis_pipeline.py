"""Task-agnostic analysis pipeline coordinator.

Computes each requirement node's IR, constraint/boundary report, decomposition
plan, and test matrix exactly once, then writes a single pipeline artifact plus
a run-level index. It is shadow-only: it does not inject prompts and does not
alter the existing execution flow.
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
from test_matrix_generator import TestMatrixReport, generate_test_matrix


SCHEMA_VERSION = "1.0"
ANALYZER_NAME = "analysis-pipeline/1.0"


@dataclass
class PipelineArtifact:
    id: str
    name: str = ""
    ir: RequirementIR | None = None
    constraints: ConstraintBoundaryReport | None = None
    plan: DecompositionPlan | None = None
    matrix: TestMatrixReport | None = None
    status: str = "complete"
    warnings: list[str] = field(default_factory=list)
    schema_version: str = SCHEMA_VERSION
    analyzer: str = ANALYZER_NAME


@dataclass
class PipelineIndex:
    nodes: list[str] = field(default_factory=list)
    files: dict[str, str] = field(default_factory=dict)
    status: str = "complete"
    schema_version: str = SCHEMA_VERSION
    analyzer: str = ANALYZER_NAME


def _safe_id(value: Any) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "unknown")).strip("._")
    return safe or "unknown"


def analyze_node_pipeline(node: dict) -> PipelineArtifact:
    """Compute one node's downstream artifacts once and reuse them."""
    node_id = str(node.get("id") or "unknown")
    name = str(node.get("name") or "")
    warnings: list[str] = []
    ir: RequirementIR | None = None
    constraints: ConstraintBoundaryReport | None = None
    plan: DecompositionPlan | None = None
    matrix: TestMatrixReport | None = None

    try:
        ir = analyze_requirement(node)
    except Exception as exc:  # noqa: BLE001
        return PipelineArtifact(
            id=node_id,
            name=name,
            status="failed",
            warnings=[f"requirement analysis failed: {exc}"],
        )

    try:
        constraints = analyze_constraint_boundaries(ir)
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"constraint/boundary analysis failed: {exc}")

    try:
        plan = decompose_requirement(ir, constraints)
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"requirement decomposition failed: {exc}")

    try:
        matrix = generate_test_matrix(ir, constraints, plan)
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"test matrix generation failed: {exc}")

    status = "partial" if warnings else "complete"
    return PipelineArtifact(
        id=node_id,
        name=name,
        ir=ir,
        constraints=constraints,
        plan=plan,
        matrix=matrix,
        status=status,
        warnings=warnings,
    )


def build_pipeline_artifacts(nodes: list[dict]) -> dict[str, PipelineArtifact]:
    """Compute pipeline artifacts once for all nodes, keyed by safe node id."""
    artifacts: dict[str, PipelineArtifact] = {}
    for node in nodes:
        try:
            artifacts[_safe_id(node.get("id"))] = analyze_node_pipeline(node)
        except Exception:  # noqa: BLE001
            continue
    return artifacts


def build_analysis_digest(artifact: PipelineArtifact | None, max_chars: int = 800) -> str:
    """Build a small, generic prompt digest from a completed pipeline artifact."""
    if artifact is None or artifact.ir is None:
        return ""
    ir = artifact.ir
    lines: list[str] = []

    def add(label: str, values: list[Any], limit: int = 8) -> None:
        clean = [str(value).strip() for value in (values or []) if str(value).strip()]
        if clean:
            lines.append(f"{label}: " + ", ".join(clean[:limit]))

    add("Entities", ir.entities)
    add("States", ir.states)
    add("Actions", ir.actions)
    add("Constraints", ir.constraints)
    add("Input rules", ir.input_rules)
    add("Output rules", ir.output_rules)
    add("Error cases", ir.error_cases)
    if artifact.constraints:
        add("Constraint gaps", artifact.constraints.coverage_gaps, limit=4)
    if artifact.matrix:
        coverage = artifact.matrix.coverage_summary or {}
        if coverage:
            lines.append("Test coverage: " + ", ".join(f"{key}={value}" for key, value in coverage.items()))
        add("Test gaps", artifact.matrix.gaps, limit=4)
    if artifact.plan:
        areas = [f"{area.category}: {area.hint}" for area in artifact.plan.test_areas if getattr(area, "hint", "")]
        if areas:
            lines.append("Test areas: " + "; ".join(areas[:6]))
    digest = "\n".join(lines)
    if len(digest) > max_chars:
        digest = digest[:max_chars].rstrip() + "\n..."
    return digest


def build_pipeline_index(nodes: list[dict], artifacts: list[PipelineArtifact]) -> PipelineIndex:
    """Build a run-level index over completed pipeline artifacts."""
    files: dict[str, str] = {}
    node_ids: list[str] = []
    for node, artifact in zip(nodes, artifacts):
        safe = _safe_id(node.get("id"))
        node_ids.append(safe)
        files[safe] = f".arc/analysis/{safe}.pipeline.json"
    status = "partial" if any(artifact.status != "complete" for artifact in artifacts) else "complete"
    return PipelineIndex(nodes=node_ids, files=files, status=status)


def write_pipeline_reports(
    output_dir: Path,
    nodes: list[dict],
    enabled: bool = True,
    artifacts: dict[str, PipelineArtifact] | None = None,
) -> list[str]:
    """Write per-node pipeline artifacts and a run-level index."""
    if not enabled:
        return []
    analysis_dir = output_dir / ".arc" / "analysis"
    analysis_dir.mkdir(parents=True, exist_ok=True)
    if artifacts is None:
        artifacts = build_pipeline_artifacts(nodes)
    artifact_list: list[PipelineArtifact] = []
    matched_nodes: list[dict] = []
    written: list[str] = []
    for node in nodes:
        artifact = artifacts.get(_safe_id(node.get("id")))
        if artifact is None:
            continue
        artifact_list.append(artifact)
        matched_nodes.append(node)
        safe = _safe_id(node.get("id"))
        path = analysis_dir / f"{safe}.pipeline.json"
        path.write_text(
            json.dumps(asdict(artifact), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        written.append(path.relative_to(output_dir).as_posix())

    index = build_pipeline_index(matched_nodes, artifact_list)
    index_path = analysis_dir / "pipeline-index.json"
    index_path.write_text(
        json.dumps(asdict(index), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    written.append(index_path.relative_to(output_dir).as_posix())
    return written
