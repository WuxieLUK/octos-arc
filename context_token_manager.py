"""Task-agnostic context and token manager.

This module is a small, pure policy layer for keeping prompt/context growth
within a configurable budget. It estimates token counts, checks whether a
candidate context fits the available prompt budget, and writes a shadow report
for observational integration. It does not inspect task-specific fields,
routes, buttons, or test values.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
import math
import os
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "1.0"
ANALYZER_NAME = "context-token-manager/1.0"
DEFAULT_CONTEXT_LIMIT_TOKENS = 1_048_576
DEFAULT_OUTPUT_RESERVE_TOKENS = 32_768


@dataclass
class ContextPolicy:
    context_limit_tokens: int = DEFAULT_CONTEXT_LIMIT_TOKENS
    output_reserve_tokens: int = DEFAULT_OUTPUT_RESERVE_TOKENS
    prompt_budget_tokens: int = 0
    trim_prompt: bool = True
    drop_shell: bool = True
    prefer_selective_source_quoting: bool = True
    max_total_tokens: int = -1
    max_turns: int = -1

    def __post_init__(self) -> None:
        if self.prompt_budget_tokens <= 0:
            self.prompt_budget_tokens = max(
                0, self.context_limit_tokens - self.output_reserve_tokens
            )


@dataclass
class ContextAssessment:
    used_tokens: int
    limit_tokens: int
    reserve_tokens: int
    remaining_tokens: int
    fits: bool
    action: str
    note: str


@dataclass
class NodeContextEstimate:
    id: str
    description_chars: int
    estimated_description_tokens: int
    spec_chars: int = 0
    estimated_spec_tokens: int = 0
    total_estimated_tokens: int = 0
    fits_in_context: bool = True


@dataclass
class ContextTokenReport:
    policy: ContextPolicy
    nodes: list[NodeContextEstimate] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    schema_version: str = SCHEMA_VERSION
    analyzer: str = ANALYZER_NAME


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def build_context_policy() -> ContextPolicy:
    """Build a generic context policy from configuration knobs."""
    return ContextPolicy(
        context_limit_tokens=_env_int("OCTOS_ARC_CONTEXT_LIMIT_TOKENS", DEFAULT_CONTEXT_LIMIT_TOKENS),
        output_reserve_tokens=_env_int("OCTOS_ARC_MAX_TOKENS", DEFAULT_OUTPUT_RESERVE_TOKENS),
        trim_prompt=os.environ.get("OCTOS_ARC_TRIM_PROMPT", "1") != "0",
        drop_shell=os.environ.get("OCTOS_ARC_DROP_SHELL", "1") != "0",
        max_total_tokens=_env_int("OCTOS_ARC_MAX_TOTAL_TOKENS", -1),
        max_turns=_env_int("OCTOS_ARC_MAX_TURNS", -1),
    )


def estimate_text_tokens(text: Any) -> int:
    """Estimate token count with a deterministic byte-based heuristic."""
    if text is None:
        return 0
    byte_len = len(str(text).encode("utf-8", errors="ignore"))
    return max(1, math.ceil(byte_len / 4)) if byte_len else 0


def assess_context(
    used_tokens: int,
    policy: ContextPolicy | None = None,
    limit_tokens: int | None = None,
    reserve_tokens: int | None = None,
) -> ContextAssessment:
    """Check whether ``used_tokens`` fits the remaining prompt budget."""
    selected = policy or build_context_policy()
    limit = selected.context_limit_tokens if limit_tokens is None else limit_tokens
    reserve = selected.output_reserve_tokens if reserve_tokens is None else reserve_tokens
    available = max(0, limit - reserve)
    remaining = max(0, available - used_tokens)
    fits = used_tokens <= available
    if fits:
        action = "ok"
        note = f"{used_tokens} estimated tokens fit within the {available}-token prompt budget."
    else:
        action = "trim"
        note = (
            f"{used_tokens} estimated tokens exceed the {available}-token prompt budget "
            f"by {used_tokens - available} tokens; trim low-relevance sections or omit redundant sources."
        )
    return ContextAssessment(
        used_tokens=used_tokens,
        limit_tokens=limit,
        reserve_tokens=reserve,
        remaining_tokens=remaining,
        fits=fits,
        action=action,
        note=note,
    )


def context_codegen_fits(
    spec_text: str,
    source_text: str = "",
    policy: ContextPolicy | None = None,
) -> bool:
    """Decide whether a codegen request fits the token budget.

    This is intentionally independent of the harness's character-based codegen
    heuristic: token pressure can be high even when characters are under a
    configured character limit, especially for non-ASCII requirements.
    """
    selected = policy or build_context_policy()
    estimated = estimate_text_tokens(spec_text) + estimate_text_tokens(source_text)
    return assess_context(estimated, policy=selected).fits


def context_source_char_budget(
    base_chars: int,
    other_estimated_tokens: int = 0,
    policy: ContextPolicy | None = None,
) -> int:
    """Return a character budget for quoted sources after reserving prompt space."""
    selected = policy or build_context_policy()
    if base_chars <= 0:
        return 0
    remaining_tokens = max(
        0,
        selected.prompt_budget_tokens - other_estimated_tokens,
    )
    token_chars = remaining_tokens * 4  # same byte/4 heuristic as estimate_text_tokens
    return max(0, min(base_chars, token_chars))


def format_context_brief(
    policy: ContextPolicy | None = None,
    used_tokens: int = 0,
) -> str:
    """Render a small, task-agnostic context budget note for model prompts."""
    selected = policy or build_context_policy()
    assessment = assess_context(used_tokens, policy=selected)
    lines = [
        "[context budget]",
        f"Prompt budget: {selected.prompt_budget_tokens} estimated tokens "
        f"(limit {assessment.limit_tokens}, output reserve {assessment.reserve_tokens}); "
        f"currently estimated usage {assessment.used_tokens}.",
        f"Budget status: {assessment.action}.",
    ]
    if assessment.action == "trim":
        lines.append(assessment.note)
    lines.append(
        "Context policy: trim_prompt={}, drop_shell={}, selective_source_quoting={}.".format(
            str(selected.trim_prompt).lower(),
            str(selected.drop_shell).lower(),
            str(selected.prefer_selective_source_quoting).lower(),
        )
    )
    if selected.max_total_tokens > 0:
        lines.append(f"Run token ceiling: {selected.max_total_tokens}; stop before it is exhausted.")
    if selected.max_turns > 0:
        lines.append(f"Run turn ceiling: {selected.max_turns}; stop before it is exhausted.")
    return "\n".join(lines) + "\n"


def build_node_context_estimate(
    node: dict,
    spec_text: str = "",
    policy: ContextPolicy | None = None,
) -> NodeContextEstimate:
    """Estimate the context cost for one requirement node."""
    selected = policy or build_context_policy()
    node_id = str(node.get("id") or "unknown")
    description = " ".join(
        [str(node.get("description") or ""), str(node.get("name") or "")]
    ).strip()
    spec_text = spec_text or ""
    desc_tokens = estimate_text_tokens(description)
    spec_tokens = estimate_text_tokens(spec_text)
    total_tokens = desc_tokens + spec_tokens
    assessment = assess_context(total_tokens, policy=selected)
    return NodeContextEstimate(
        id=node_id,
        description_chars=len(description),
        estimated_description_tokens=desc_tokens,
        spec_chars=len(spec_text),
        estimated_spec_tokens=spec_tokens,
        total_estimated_tokens=total_tokens,
        fits_in_context=assessment.fits,
    )


def build_context_token_report(
    nodes: list[dict],
    spec_texts: dict[str, str] | None = None,
) -> ContextTokenReport:
    """Build a generic run-level context budget report."""
    policy = build_context_policy()
    spec_texts = spec_texts or {}
    estimates: list[NodeContextEstimate] = []
    for node in nodes:
        node_id = str(node.get("id") or "")
        spec_text = spec_texts.get(node_id, "")
        estimates.append(build_node_context_estimate(node, spec_text, policy))
    recommendations: list[str] = []
    if any(not estimate.fits_in_context for estimate in estimates):
        recommendations.append(
            "Reduce per-turn prompt growth by quoting only relevant spec/source excerpts."
        )
    if policy.prompt_budget_tokens <= 0:
        recommendations.append(
            "Configure a context limit larger than the output reserve."
        )
    if policy.max_total_tokens > 0:
        recommendations.append(
            f"Stop or simplify before the run-level {policy.max_total_tokens}-token budget is exhausted."
        )
    if not recommendations:
        recommendations.append(
            "Prompt budgets are sufficient for the estimated requirement context."
        )
    return ContextTokenReport(
        policy=policy,
        nodes=estimates,
        recommendations=recommendations,
    )


def write_context_token_report(
    output_dir: Path,
    nodes: list[dict],
    spec_texts: dict[str, str] | None = None,
    enabled: bool = True,
) -> list[str]:
    """Write a shadow context/token report under ``.arc/analysis/context-token-manager.json``."""
    if not enabled:
        return []
    analysis_dir = output_dir / ".arc" / "analysis"
    analysis_dir.mkdir(parents=True, exist_ok=True)
    report = build_context_token_report(nodes, spec_texts)
    path = analysis_dir / "context-token-manager.json"
    path.write_text(
        json.dumps(asdict(report), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return [path.relative_to(output_dir).as_posix()]
