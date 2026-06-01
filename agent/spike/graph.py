"""S1 — the LangGraph task graph (the seam's middle edge, execution §2/§7).

PERCEIVE → PROPOSE(deterministic) → ⟨CONSENT⟩ interrupt(ConsentRequest)
        → on Command(resume=ConsentDecision) → ACT(fill, IDEMPOTENT) → CONFIRM.

Built against the frozen `clarion.contracts` (ClarionState, Proposal, Action,
Consent, ConsentRequest, ConsentDecision, Observation) and the real `Actuator`
port — here the `MinActuator` (Playwright/CDP). langgraph 1.2.2: `interrupt` /
`Command` from `langgraph.types`, `InMemorySaver` (the C1-locked versions).

PROPOSE is DETERMINISTIC for the spike (fill the "Full name" field with a fixed
value) — the seam is the point, not model reasoning. It is structured so a Gemini
planner drops in later by replacing `_propose` with a model call; everything
downstream (consent / idempotent act / confirm) is unchanged.

THE IDEMPOTENCY GOTCHA (execution §2.3, load-bearing): on `Command(resume=)` the
interrupted CONSENT node re-executes from the top, and ACT runs again. So ACT
must be idempotent. We guard it with a `consent_log` once-check: ACT only fills if
this proposal_id is approved AND has not already been acted on (tracked via a
TraceEvent "acted" marker in `trace`). A second `resume(approve)` therefore does
NOT double-fill.
"""

from __future__ import annotations

import time
from typing import Optional

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.constants import START
from langgraph.graph import StateGraph
from langgraph.types import Command, interrupt

# Allowlist the contract models for the checkpointer's msgpack serde (execution
# §18.6 K1 action item). langgraph 1.2.2 deserializes our pydantic models today
# but logs a future-removal warning; this keeps the §2.1 durability claim valid
# without touching contracts/ (pure adapter-side setting). S1 validates the §18.6
# guidance is correct.
_ALLOWED_MSGPACK_MODULES = [
    ("clarion.contracts.state", "SelectorMap"),
    ("clarion.contracts.state", "AxNode"),
    ("clarion.contracts.state", "Proposal"),
    ("clarion.contracts.state", "Action"),
    ("clarion.contracts.state", "Consent"),
    ("clarion.contracts.state", "TraceEvent"),
    ("clarion.contracts.state", "Fact"),
    ("clarion.contracts.state", "Stage"),
]


def _make_checkpointer() -> InMemorySaver:
    return InMemorySaver(
        serde=JsonPlusSerializer(allowed_msgpack_modules=_ALLOWED_MSGPACK_MODULES)
    )

from clarion.contracts.events import ConsentDecision, ConsentRequest
from clarion.contracts.ports import Actuator
from clarion.contracts.state import (
    Action,
    ClarionState,
    Consent,
    Proposal,
    TraceEvent,
)

# The deterministic spike target: fill the "Full name" field with this value.
SPIKE_FILL_VALUE = "Jane Smith"
SPIKE_PROPOSAL_ID = "spike-fill-fullname"


def _trace(node: str, event: str = "info", **data: object) -> TraceEvent:
    return TraceEvent(node=node, event=event, at=time.time(), data=dict(data))


def _find_name_field(state: ClarionState) -> Optional[int]:
    """The deterministic 'planner': pick the first textbox in the page_index.
    A Gemini planner would replace this selection step."""
    page = state["page_index"]
    for idx, node in page.nodes.items():
        if node.role in ("textbox", "searchbox"):
            return idx
    return None


def build_spike_graph(actuator: Actuator):
    """Compile the PERCEIVE→PROPOSE→⟨CONSENT⟩→ACT→CONFIRM graph over `actuator`.

    Returns the compiled graph (InMemorySaver-backed). Caller drives it with a
    `thread_id` config: first `ainvoke(seed)` runs to the interrupt; resume with
    `ainvoke(Command(resume=ConsentDecision(...).model_dump()))`.
    """

    async def perceive(state: ClarionState) -> dict:
        sm = await actuator.perceive()
        return {
            "page_index": sm,
            "trace": [_trace("PERCEIVE", "exit", nodes=len(sm.nodes))],
        }

    def propose(state: ClarionState) -> dict:
        """DETERMINISTIC propose (spike): fill the name field with the fixed value."""
        idx = _find_name_field(state)
        if idx is None:
            return {"trace": [_trace("PROPOSE", "info", error="no-name-field")]}
        node = state["page_index"].nodes[idx]
        proposal = Proposal(
            id=SPIKE_PROPOSAL_ID,
            utterance=(
                f"I found the {node.name or 'name'} field. "
                f"I'll fill it with {SPIKE_FILL_VALUE}. Say yes to continue."
            ),
            action=Action(kind="fill", index=idx, value=SPIKE_FILL_VALUE),
            irreversible=False,
        )
        return {
            "pending_proposal": proposal,
            "trace": [_trace("PROPOSE", "exit", proposal_id=proposal.id)],
        }

    def consent(state: ClarionState) -> dict:
        """⟨CONSENT⟩ — surface the ConsentRequest and pause. On resume the node
        re-executes from the top; that is fine because it has no side-effects of
        its own (the side-effect lives in ACT, which is guarded)."""
        proposal = state["pending_proposal"]
        assert proposal is not None
        decision_payload = interrupt(
            ConsentRequest(
                proposal_id=proposal.id,
                utterance=proposal.utterance,
                irreversible=proposal.irreversible,
            ).model_dump()
        )
        decision = ConsentDecision.model_validate(decision_payload)
        return {
            "consent_log": [
                Consent(
                    proposal_id=proposal.id,
                    decision=decision.decision,
                    value=decision.value,
                    at=time.time(),
                )
            ],
            "trace": [_trace("CONSENT", "exit", decision=decision.decision)],
        }

    async def act(state: ClarionState) -> dict:
        """ACT — execute the approved fill, IDEMPOTENTLY (execution §2.3).

        Guard: only act if (1) the proposal is approved in consent_log, AND
        (2) we have not already recorded an 'acted' trace marker for it. A second
        resume(approve) re-enters here but the once-flag stops the double-fill.
        """
        proposal = state["pending_proposal"]
        assert proposal is not None and proposal.action is not None

        approved = any(
            c.proposal_id == proposal.id and c.decision == "approve"
            for c in state["consent_log"]
        )
        already_acted = any(
            ev.node == "ACT"
            and ev.event == "info"
            and ev.data.get("acted_proposal_id") == proposal.id
            for ev in state["trace"]
        )

        if not approved:
            return {"trace": [_trace("ACT", "info", skipped="not-approved")]}
        if already_acted:
            # IDEMPOTENT: the once-flag is already set → do NOT fill again.
            return {
                "trace": [_trace("ACT", "info", skipped="already-acted",
                                 acted_proposal_id=proposal.id)]
            }

        obs = await actuator.act(proposal.action)
        return {
            "page_index": obs.selector_map,
            # The once-flag marker — its presence makes a re-entry idempotent.
            "trace": [
                _trace("ACT", "info", acted_proposal_id=proposal.id,
                       success=obs.success),
                _trace("ACT", "exit", success=obs.success),
            ],
        }

    async def confirm(state: ClarionState) -> dict:
        """CONFIRM — re-perceive and record. The caller asserts the live field
        value via a CDP read-back (the honest 'it was actually filled' check)."""
        sm = await actuator.perceive()
        return {
            "page_index": sm,
            "trace": [_trace("CONFIRM", "exit", nodes=len(sm.nodes))],
        }

    builder = StateGraph(ClarionState)
    builder.add_node("perceive", perceive)
    builder.add_node("propose", propose)
    builder.add_node("consent", consent)
    builder.add_node("act", act)
    builder.add_node("confirm", confirm)
    builder.add_edge(START, "perceive")
    builder.add_edge("perceive", "propose")
    builder.add_edge("propose", "consent")
    builder.add_edge("consent", "act")
    builder.add_edge("act", "confirm")
    return builder.compile(checkpointer=_make_checkpointer())


def seed_state(goal: str = "fill in my name", mode: str = "normal") -> ClarionState:
    """A minimal valid ClarionState to start the spike graph."""
    from clarion.contracts.state import SelectorMap

    return ClarionState(
        goal=goal,
        mode=mode,  # type: ignore[typeddict-item]
        plan=[],
        stage_idx=0,
        step=(0, 1),
        page_index=SelectorMap(),
        grounded_facts=[],
        pending_proposal=None,
        consent_log=[],
        trace=[],
    )


__all__ = ["build_spike_graph", "seed_state", "SPIKE_FILL_VALUE", "SPIKE_PROPOSAL_ID"]
