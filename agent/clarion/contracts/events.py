"""Clarion contracts — the plane↔plane and plane↔frontend event protocol
(execution §18.4).

This is the wire between the voice plane and the task plane, and between the task
plane and the on-screen panel. It is **pure** pydantic v2 — it does NOT import
livekit. The ``@function_tool`` wrapper that actually registers ``advance_task``
with LiveKit lives in V1, not here; this module only documents the signature and
pins the message shapes (execution §18.4 / §18.5 freeze rule).
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from clarion.contracts.state import Fact, TraceEvent

# ---------------------------------------------------------------------------
# Voice plane → task plane
# ---------------------------------------------------------------------------


class AdvanceTaskRequest(BaseModel):
    """Payload for the voice plane's ``advance_task`` tool call.

    advance_task signature (implemented in V1 as a LiveKit ``@function_tool``;
    documented here, NOT imported)::

        advance_task(user_intent: str | None) -> str

    Contract (execution §5, load-bearing): the tool MUST be non-blocking. The V1
    wrapper launches the graph step (``asyncio.ensure_future``) then awaits
    ``speech_handle.wait_if_not_interrupted([task])``. On barge-in,
    ``speech_handle.interrupted`` is True → ``task.cancel()`` → return None. The
    graph keeps running in the background after the agent's sentence ends.
    """

    user_intent: Optional[str] = None


# ---------------------------------------------------------------------------
# Task plane → voice plane (the interrupt() payload at ⟨CONSENT⟩)
# ---------------------------------------------------------------------------


class SourceRef(BaseModel):
    """Node-IDENTITY payload for the source-node highlight (the epistemic-clause
    proof surface — companion to the action-trace feed). Carried on a
    ``ConsentRequest`` so the voice plane can outline, on the live page, the SAME
    node the agent resolved to act, and mirror the PROVEN label pairing as a panel
    row — all from real AX node identity, never ``bbox`` or LLM-printed text.

    Populated by the kernel ``consent`` node, which holds the correct live
    ``page_index`` + ``paired_facts`` at interrupt time (the parent stage state is
    NOT yet committed there — the executor node is suspended). Additive + optional:
    a plain consent without it is the unchanged shape; SIGHTED-observer-only, so the
    product never depends on any field here."""

    # The live SelectorMap index of the field/target node → ``actuator.highlight``
    # resolves it index→backendDOMNodeId EXACTLY like the click. None → nothing to
    # outline (a read-back / clarify).
    index: Optional[int] = None
    # The field's AX node_id + accessible name (the panel row).
    node_id: str = ""
    name: str = ""
    # The PROVEN paired label (a ``PairedFact`` whose VALUE half IS this field) and
    # HOW it was joined — never reading-order; empty when no pairing backs it.
    label_text: str = ""
    method: str = ""


class ConsentRequest(BaseModel):
    """The value a LangGraph ``interrupt()`` surfaces at the consent gate. The
    voice plane speaks ``utterance`` and waits for a decision (execution §2.3,
    §18.4)."""

    proposal_id: str
    # Speak this readback before acting.
    utterance: str
    irreversible: bool = False
    options: list[str] = Field(default_factory=lambda: ["yes", "no", "edit"])
    # The source-node highlight payload (epistemic-clause proof surface). Additive +
    # optional → a consent without it is the unchanged shape.
    source: Optional[SourceRef] = None


class ConsentDecision(BaseModel):
    """The resume value: ``Command(resume=ConsentDecision(...))`` (execution
    §2.3). ``respond`` routes an ``ask_user`` clarification back through the voice
    plane rather than approving/rejecting (execution §2.3)."""

    decision: Literal["approve", "reject", "edit", "respond"]
    value: Optional[str] = None


# ---------------------------------------------------------------------------
# Task plane → frontend (published as a LiveKit participant attribute, JSON)
# ---------------------------------------------------------------------------


class PanelState(BaseModel):
    """Published as a LiveKit participant attribute (JSON) to drive the six §6
    effects on the on-screen panel (execution §6, §18.4). The blind user never
    needs this; the panel never speaks — two audiences, one state."""

    stage: str
    # (k, n) within the current stage → "2 fields left".
    step: tuple[int, int]
    proposal: Optional[str] = None
    consent_state: Literal["idle", "awaiting_yes", "approved", "rejected"] = "idle"
    grounded_facts: list[Fact] = Field(default_factory=list)
    # Live Moss number vs the greyed cold-RAG baseline (the latency meter, §8).
    retrieval_ms: Optional[float] = None
    baseline_ms: Optional[float] = None
    trace_tail: list[TraceEvent] = Field(default_factory=list)


__all__ = [
    "AdvanceTaskRequest",
    "SourceRef",
    "ConsentRequest",
    "ConsentDecision",
    "PanelState",
]
