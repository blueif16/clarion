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

from typing import Literal, Optional

from pydantic import BaseModel, Field

from clarion.contracts.events import PanelState
from clarion.contracts.state import ClarionState, TraceEvent

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


# ---------------------------------------------------------------------------
# The ACTIVITY projection — trace + consent_log → one record per decided action
# (the action-side analog of the source-node panel). ONE schema feeds BOTH the
# HUD toast feed (Feature A) and the ``read_history`` voice tool (Feature B), so
# the two surfaces can never disagree about what we did.
#
# This is a PURE projection over the append-only audit channels — it adds NO new
# kernel state and changes NO frozen contract. ``ActivityItem`` is a derived view
# object (it lives here, not in frozen ``contracts/``); it is published as raw JSON
# over room data and read back by the voice tool, never persisted.
# ---------------------------------------------------------------------------

# The kernel nodes whose trace events are DECISION-BEARING (a proposal lifecycle).
# Node enter/exit of ground/verify/confirm/planner/executor are deliberately NOT
# surfaced as activity — they are control flow, not decisions (avoids toast spam).
_DECISION_BEARING_NODES = frozenset({"PROPOSE", "GATE", "CONSENT", "ACT"})

# Terminal statuses — an action here is resolved (the HUD may let its toast fade).
RESOLVED_STATUSES = frozenset({"done", "failed", "rejected", "abstained", "approved"})


class ActivityItem(BaseModel):
    """One decided action, folded from every trace event that shares its
    ``proposal_id``. The short fields drive the toast / spoken line; ``details``
    carries EVERY real recorded field for the action (scratch reasoning, the
    irreversibility rationale, the classification, decide_ms, intent, phase,
    done_check, success, source node, timestamps, …) so the expanded card can show
    all of it. NOTHING here is fabricated — every value comes from what the kernel
    actually wrote to ``TraceEvent.data``."""

    proposal_id: str
    kind: str = ""            # read | fill | click | navigate
    target: str = ""          # the live AX node name the action points at
    value: str = ""           # the grounded filled/spoken value (extract, never generate)
    status: str = "proposed"  # proposed|awaiting_yes|approved|done|failed|rejected|abstained
    irreversibility: str = "" # reversible | irreversible | unknown
    decision: str = ""        # approve | reject | edit | respond (the consent answer)
    at: float = 0.0           # the most recent event time for this action
    details: dict = Field(default_factory=dict)

    @property
    def resolved(self) -> bool:
        return self.status in RESOLVED_STATUSES

    @property
    def persist(self) -> bool:
        """Should the HUD toast PERSIST (not auto-vanish)? Stakes-driven: an
        irreversible / unknown step, or one still awaiting a yes, stands out until
        it resolves. A plain reversible read/navigation may fade."""
        if self.status == "awaiting_yes":
            return True
        if self.irreversibility in ("irreversible", "unknown") and not self.resolved:
            return True
        return False


def _proposal_id_of(event: TraceEvent) -> Optional[str]:
    """The proposal a trace event belongs to: PROPOSE/GATE/CONSENT carry
    ``proposal_id``; ACT's once-flag marker carries ``acted_proposal_id``. Events
    with neither (ACT.exit, the not-approved skip, control-flow nodes) are not part
    of a decided-action group."""
    data = event.data or {}
    pid = data.get("proposal_id") or data.get("acted_proposal_id")
    return str(pid) if pid else None


def _status_of(details: dict) -> str:
    """Derive the action's status from the merged real fields, honest-decline
    first. Priority: abstained (couldn't ground / prove it) ▸ rejected ▸ acted
    (done/failed) ▸ approved ▸ awaiting_yes (gated) ▸ proposed."""
    if details.get("rejected") or details.get("hedged") or details.get("abstained"):
        return "abstained"
    if details.get("decision") == "reject":
        return "rejected"
    if "acted_proposal_id" in details:
        return "done" if details.get("success", True) else "failed"
    if details.get("decision") == "approve":
        return "approved"
    if (
        details.get("classification") in ("irreversible", "unknown")
        or details.get("gates")
        or details.get("irreversible")
    ):
        return "awaiting_yes"
    return "proposed"


def activity_items(state: ClarionState) -> list[ActivityItem]:
    """Fold ``trace`` + ``consent_log`` into one ``ActivityItem`` per decided
    action, in the order the actions began (chronological). Decision-bearing events
    only. This is the single source both the HUD feed and ``read_history`` read."""
    trace = list(state.get("trace") or [])
    consent_log = list(state.get("consent_log") or [])

    groups: dict[str, dict] = {}
    order: list[str] = []
    for event in trace:
        if getattr(event, "node", "") not in _DECISION_BEARING_NODES:
            continue
        pid = _proposal_id_of(event)
        if pid is None:
            continue
        if pid not in groups:
            groups[pid] = {"details": {}, "last_at": event.at}
            order.append(pid)
        group = groups[pid]
        # Merge EVERY real field (latest-wins on the rare key collision); keep all.
        group["details"].update(dict(event.data or {}))
        group["last_at"] = max(group["last_at"], event.at)

    # Fold the consent_log decision/value too (redundant-safe with CONSENT.exit, but
    # robust if the trace tail was clipped). Never invents a group.
    for consent in consent_log:
        pid = getattr(consent, "proposal_id", "")
        if pid in groups:
            group = groups[pid]
            group["details"].setdefault("decision", consent.decision)
            if consent.value:
                group["details"]["consent_value"] = consent.value
            group["last_at"] = max(group["last_at"], consent.at or group["last_at"])

    items: list[ActivityItem] = []
    for pid in order:
        details = groups[pid]["details"]
        items.append(
            ActivityItem(
                proposal_id=pid,
                kind=str(details.get("action_kind") or ""),
                target=str(details.get("target_name") or ""),
                value=str(details.get("say") or details.get("value_ref") or ""),
                status=_status_of(details),
                irreversibility=str(
                    details.get("classification")
                    or ("irreversible" if details.get("irreversible") else "")
                ),
                decision=str(details.get("decision") or ""),
                at=groups[pid]["last_at"],
                details=dict(details),
            )
        )
    return items


# ---------------------------------------------------------------------------
# The grounded voice readback (Feature B) — a kernel-authored history say the
# voice plane speaks VERBATIM. Built ONLY from recorded fields (the same firewall
# as the panel: the history has a real source in the trace, so it is structurally
# speakable and cannot be confabulated by the voice LLM).
# ---------------------------------------------------------------------------


def _action_phrase(item: ActivityItem) -> str:
    """One grounded clause for an action, in the persona register (you're in
    command; name each step plainly). Uses only the action's real fields."""
    target = item.target
    value = item.value
    if item.status == "abstained":
        what = f" on {target}" if target else ""
        return f"I held back{what} because I couldn't confirm it from the page"
    if item.status == "rejected":
        what = f" {target}" if target else " that step"
        return f"you declined{what}"
    if item.status in ("awaiting_yes", "approved"):
        if item.kind == "fill" and target:
            return f"I'm at {target}, waiting on your yes"
        what = target or "a step"
        return f"I'm waiting on your yes for {what}"
    # done / failed / proposed — describe what the action was, grounded.
    if item.kind == "read":
        if target and value:
            return f"I read {target}, {value}"
        if value:
            return f"I read {value}"
        return f"I read {target}" if target else "I read the page"
    if item.kind == "fill":
        if target and value:
            return f"I filled {target} with {value}"
        return f"I filled {target}" if target else "I filled a field"
    if item.kind == "navigate":
        return f"I opened {target}" if target else "I moved to a new page"
    if item.kind == "click":
        return f"I selected {target}" if target else "I selected a control"
    return f"I worked on {target}" if target else "I took a step"


def _orientation(item: ActivityItem) -> str:
    """The closing where-are-we line, from the most recent action's real status."""
    if item.status == "awaiting_yes":
        where = f" at {item.target}" if item.target else ""
        return f"You're{where} now — say yes when you're ready, or no to skip."
    if item.status == "abstained":
        return "You're in command — tell me how you'd like to proceed."
    return "You're in command — tell me the next step."


def format_history_say(items: list[ActivityItem], n: int = 3) -> str:
    """The grounded, spoken-verbatim readback of the last ``n`` decided actions.
    Empty-history is an honest, non-fabricated line. Persona register; no banned
    role words (copy_lint-clean)."""
    recent = items[-n:] if n and n > 0 else list(items)
    if not recent:
        return (
            "We haven't taken any steps yet. Tell me what you'd like to do and "
            "I'll read it back before I start."
        )
    total = len(recent)
    clauses: list[str] = []
    for i, item in enumerate(recent):
        back = total - 1 - i
        if back == 0:
            label = "Last"
        elif back == 1:
            label = "One step back"
        elif back == 2:
            label = "Two steps back"
        else:
            label = f"{back} steps back"
        clauses.append(f"{label}: {_action_phrase(item)}")
    body = ". ".join(clauses)
    return f"{body}. {_orientation(recent[-1])}"


__all__ = [
    "to_panel_state",
    "ActivityItem",
    "activity_items",
    "format_history_say",
    "RESOLVED_STATUSES",
]
