"""Task-agnostic repair trigger decision.

This module decides *whether* a detected failure should launch a repair turn.
It is intentionally small and pure: it receives only failure metadata and
repair history, never task names, requirement text, pages, selectors, or tests.

Decision states:
    REPAIR           launch the existing repair turn
    RETRY_VERIFY     do not spend an LLM turn yet; re-run verification first
    STOP_NO_PROGRESS keep the best state and stop the current repair loop

The classification is a *feature*, not a rule. A ``timeout``, ``visibility``,
or ``startup`` classification can still represent a real code defect, so it
never maps directly to a stop decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

REPAIR = "REPAIR"
RETRY_VERIFY = "RETRY_VERIFY"
STOP_NO_PROGRESS = "STOP_NO_PROGRESS"

# These classes only make the *first* occurrence eligible for a cheap retry.
# They never, by themselves, cause STOP_NO_PROGRESS.
TRANSIENT_CLASSES = frozenset({"timeout", "startup", "visibility"})

_STARTUP_KEYWORDS = (
    "connectionrefused",
    "econnrefused",
    "connect refused",
    "eaddrinuse",
    "econnreset",
    "cannot assign requested address",
    "address already in use",
)
_VISIBILITY_KEYWORDS = (
    "not visible",
    "to be visible",
    "tobevisible",
    "element is not visible",
)


@dataclass(frozen=True)
class RepairDecisionContext:
    failure_signature: Any
    previous_failure_signature: Any | None
    failure_class: str
    code_changed_since_last_repair: bool | None
    attempt: int = 0
    turn_type: str = "node"


def classify_failure(
    error_text: str | None,
    results: Iterable[tuple[str, str]] = (),
) -> str:
    """Return a coarse, task-agnostic failure class.

    ``error_text`` is an infrastructure/startup error (no test report).
    ``results`` is an iterable of ``(status, message)`` for failing tests.
    The result is only a decision feature; it is not a repair directive.
    """
    if error_text:
        lowered = error_text.lower()
        if any(keyword in lowered for keyword in _STARTUP_KEYWORDS):
            return "startup"
        return "infra"

    for status, message in results:
        message_lower = (message or "").lower()
        if status == "timedOut" or "timed out" in message_lower[:400]:
            return "timeout"

    for _, message in results:
        message_lower = (message or "").lower()
        if any(keyword in message_lower for keyword in _VISIBILITY_KEYWORDS):
            return "visibility"

    return "logic"


def decide_repair(context: RepairDecisionContext) -> str:
    """Decide the next repair action from generic metadata only.

    Conservative by design. STOP_NO_PROGRESS requires all of:
        - a previous failure exists,
        - the current failure signature matches it,
        - the previous repair did not change code.
    Anything unknown defaults to REPAIR so the gate cannot accidentally kill
    a real repair.
    """
    previous = context.previous_failure_signature
    same_failure = previous is not None and context.failure_signature == previous
    first_occurrence = previous is None

    if not same_failure:
        if first_occurrence and context.failure_class in TRANSIENT_CLASSES:
            return RETRY_VERIFY
        return REPAIR

    # Same normalized failure signature (observation, steps, location).
    if context.code_changed_since_last_repair is False:
        return STOP_NO_PROGRESS
    # Code changed but the failure did not move: the approach changed, so a
    # focused repair is still worth one attempt.
    return REPAIR
