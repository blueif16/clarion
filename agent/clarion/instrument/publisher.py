"""publisher.py — maps ClarionState → PanelState (the §6 panel wire).

``to_panel_state`` is a **pure mapping function** — no I/O, no provider SDKs,
no LiveKit. The live participant-attribute publish (``room.local_participant
.set_attributes(...)``) is I1's responsibility, not this module's. The seam
is intentional: I1 calls ``to_panel_state`` and then publishes the result.

Usage::

    panel = to_panel_state(
        state,
        retrieval_ms=timed_retriever.last_query_ms,
        baseline_ms=COLD_RAG_BASELINE_MS,
    )
    # I1 then does: await room.local_participant.set_attributes(
    #     {"panel_state": panel.model_dump_json()}
    # )

The mapping rules follow execution §6 (§18.4 event protocol):
  - ``stage``         ← name of ``plan[stage_idx]`` (or "idle" when no plan).
  - ``step``          ← ``state["step"]`` coerced to ``tuple[int,int]``
                        (JsonPlus round-trips tuples as lists; see §18.6 note).
  - ``proposal``      ← ``pending_proposal.utterance`` (or None).
  - ``consent_state`` ← derived from the most recent consent_log entry; falls
                        back to "awaiting_yes" if ``pending_proposal`` is set
                        and the log has no entry for it.
  - ``grounded_facts``← ``state["grounded_facts"]`` verbatim.
  - ``retrieval_ms``  ← caller-supplied (from TimedRetriever.last_query_ms).
  - ``baseline_ms``   ← caller-supplied (from COLD_RAG_BASELINE_MS or similar).
  - ``trace_tail``    ← the last ``_TRACE_TAIL`` events from ``state["trace"]``.

This module is **pure**: pydantic v2 + the frozen contracts only.
"""

from __future__ import annotations

from typing import Literal

from clarion.contracts.events import PanelState
from clarion.contracts.state import ClarionState

# How many trace events to include in trace_tail (keeps the JSON payload small).
_TRACE_TAIL: int = 20


def _derive_consent_state(
    state: ClarionState,
) -> Literal["idle", "awaiting_yes", "approved", "rejected"]:
    """Derive the panel's consent_state from the live kernel state.

    Decision tree:
    1. If there is no ``pending_proposal`` → "idle".
    2. If the consent_log has an entry for this proposal:
       - "approve"  → "approved"
       - "reject"   → "rejected"
       - "edit"/"respond" → "awaiting_yes"  (still needs a clean answer)
    3. If no consent_log entry for this proposal exists → "awaiting_yes"
       (the interrupt() has fired but the user has not answered yet).
    """
    proposal = state.get("pending_proposal")
    if proposal is None:
        return "idle"

    consent_log = state.get("consent_log") or []
    # Walk the log in reverse to find the most recent entry for this proposal.
    for entry in reversed(consent_log):
        if entry.proposal_id == proposal.id:
            if entry.decision == "approve":
                return "approved"
            if entry.decision == "reject":
                return "rejected"
            # edit / respond: the gate is still open.
            return "awaiting_yes"

    # No consent_log entry for this proposal → still waiting.
    return "awaiting_yes"


def to_panel_state(
    state: ClarionState,
    *,
    retrieval_ms: float | None,
    baseline_ms: float | None,
) -> PanelState:
    """Map a ``ClarionState`` snapshot into the ``PanelState`` the panel consumes.

    This is a **pure function** (no I/O). The LiveKit participant-attribute
    publish is left to I1 — keep the seam.

    Args:
        state:        The current durable kernel state.
        retrieval_ms: The most recent warm-retrieval latency (ms), from
                      ``TimedRetriever.last_query_ms``. Pass ``None`` before
                      the first retrieval.
        baseline_ms:  The greyed cold-RAG baseline (ms), typically
                      ``COLD_RAG_BASELINE_MS`` (340.0). Pass ``None`` to
                      suppress the baseline on the panel.

    Returns:
        A ``PanelState`` ready to be serialised and published.
    """
    plan = state.get("plan") or []
    stage_idx = state.get("stage_idx") or 0

    if plan and 0 <= stage_idx < len(plan):
        stage_name = plan[stage_idx].id
    else:
        stage_name = "idle"

    # Coerce step to tuple[int, int] — JsonPlus round-trips tuples as lists.
    raw_step = state.get("step") or (0, 1)
    step: tuple[int, int] = (int(raw_step[0]), int(raw_step[1]))

    proposal = state.get("pending_proposal")
    proposal_utterance = proposal.utterance if proposal is not None else None

    consent_state = _derive_consent_state(state)

    grounded_facts = list(state.get("grounded_facts") or [])

    trace = list(state.get("trace") or [])
    trace_tail = trace[-_TRACE_TAIL:] if trace else []

    return PanelState(
        stage=stage_name,
        step=step,
        proposal=proposal_utterance,
        consent_state=consent_state,
        grounded_facts=grounded_facts,
        retrieval_ms=retrieval_ms,
        baseline_ms=baseline_ms,
        trace_tail=trace_tail,
    )


__all__ = ["to_panel_state"]
