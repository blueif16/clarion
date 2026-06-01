"""K1 — the Clarion kernel as a LangGraph graph (execution §2).

    GROUND ▶ VERIFY ▶ PROPOSE ▶ ⟨CONSENT⟩ ▶ ACT ▶ CONFIRM

One specialized node per kernel verb (execution §2.2), walking the durable
``ClarionState`` that lives in the checkpointer. The two-clause policy
(``kernel.policy``) is enforced at VERIFY (epistemic) and ACT (agentic). The two
autonomy modes (foundation §5) are a single ``mode``-conditional edge into the
consent gate.

Built against the FROZEN ``clarion.contracts`` only; imports the ``Retriever`` and
``Actuator`` ports (never a real provider). The seam patterns are reused verbatim
from ``agent/spike/graph.py`` (S1, green): ``interrupt`` / ``Command`` from
``langgraph.types``, the ``InMemorySaver(serde=JsonPlusSerializer(
allowed_msgpack_modules=...))`` allowlist (execution §18.6), and the §2.3
idempotency once-flag (an ``acted_proposal_id`` TraceEvent marker that makes a
re-entry on resume a no-op).

``trace`` and ``consent_log`` are ``Annotated[list[...], operator.add]`` in the
frozen contract (re-freeze 2026-05-31), so each node returns ONLY its NEW entries
and LangGraph's reducer concatenates. The §2.3 once-flag reads the
reducer-accumulated ``trace``, which carries the prior ACT marker across an
interrupt re-execution.

langgraph 1.2.2 facts (Context7 /websites/langchain_oss_python_langgraph):
  - ``interrupt(value)`` pauses the node; on ``Command(resume=payload)`` the
    *containing node re-executes from the top* and ``interrupt()`` returns
    ``payload``. So any side-effect that precedes (or follows) the interrupt must
    be idempotent — hence ACT's once-flag (execution §2.3, load-bearing).
  - ``add_conditional_edges(node, router, mapping)`` routes by a pure function of
    state — used for the mode gate and the consent decision branch.
  - A state channel annotated ``Annotated[list[X], operator.add]`` is an
    append/reducer channel: node returns are CONCATENATED, not overwritten.
  - ``InMemorySaver(serde=JsonPlusSerializer(allowed_msgpack_modules=[...]))``
    round-trips our pydantic contract models with no future-removal warning.
"""

from __future__ import annotations

import time
from typing import Literal, Optional

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.constants import END, START
from langgraph.graph import StateGraph
from langgraph.types import interrupt

from clarion.contracts.events import ConsentDecision, ConsentRequest
from clarion.contracts.ports import Actuator, Retriever
from clarion.contracts.state import (
    Action,
    ClarionState,
    Consent,
    Proposal,
    TraceEvent,
)
from clarion.kernel.policy import (
    assert_consented,
    assert_grounded,
    is_consented,
    speakable,
)

# ---------------------------------------------------------------------------
# Checkpointer serde allowlist (execution §18.6 K1 action item; S1-validated).
# langgraph 1.2.2 still deserializes these pydantic models without the allowlist
# but logs a future-removal warning. Naming each (module, qualname) here keeps the
# §2.1 durability claim valid with zero warnings, and touches NOTHING in
# contracts/ (pure adapter-side setting).
# ---------------------------------------------------------------------------
_ALLOWED_MSGPACK_MODULES = [
    ("clarion.contracts.state", "SelectorMap"),
    ("clarion.contracts.state", "AxNode"),
    ("clarion.contracts.state", "Fact"),
    ("clarion.contracts.state", "Action"),
    ("clarion.contracts.state", "Proposal"),
    ("clarion.contracts.state", "Observation"),
    ("clarion.contracts.state", "PageDiff"),
    ("clarion.contracts.state", "Stage"),
    ("clarion.contracts.state", "Consent"),
    ("clarion.contracts.state", "TraceEvent"),
]

Mode = Literal["normal", "fast"]


def make_checkpointer() -> InMemorySaver:
    """The kernel's checkpointer: InMemorySaver with the contract-model allowlist
    so checkpointed ``ClarionState`` round-trips warning-free (execution §18.6)."""
    return InMemorySaver(
        serde=JsonPlusSerializer(allowed_msgpack_modules=_ALLOWED_MSGPACK_MODULES)
    )


def _trace(node: str, event: str = "info", **data: object) -> TraceEvent:
    return TraceEvent(node=node, event=event, at=time.time(), data=dict(data))


def _already_acted(state: ClarionState, proposal_id: str) -> bool:
    """The §2.3 once-flag check: has ACT already recorded an ``acted_proposal_id``
    marker for this proposal? If so, a re-entry (e.g. a second resume(approve))
    must NOT side-effect again.

    ``trace`` is the reducer-accumulated channel (``Annotated[list[TraceEvent],
    operator.add]`` in the frozen contract), so on an interrupt re-execution this
    sees the marker the FIRST ACT pass appended — exactly the durable once-flag
    the idempotency guarantee needs."""
    return any(
        ev.node == "ACT"
        and ev.data.get("acted_proposal_id") == proposal_id
        for ev in state["trace"]
    )


def build_kernel(
    retriever: Retriever,
    actuator: Actuator,
    mode: Mode = "normal",
):
    """Compile the GROUND→VERIFY→PROPOSE→⟨CONSENT⟩→ACT→CONFIRM kernel.

    ``mode`` is baked into the compiled graph's consent routing:
      - ``normal``: every consequential step (any proposal carrying an action)
        hits ⟨CONSENT⟩ and interrupts;
      - ``fast``: reversible proposals auto-proceed straight to ACT, but an
        ``irreversible`` proposal ALWAYS interrupts (the foundation §5 hard-stop).

    Returns the compiled graph (checkpointer-backed). Drive it with a ``thread_id``
    config: first ``ainvoke(seed)`` runs to the consent interrupt (when armed);
    resume with ``ainvoke(Command(resume=ConsentDecision(...).model_dump()))``. A
    fully auto-proceeding Fast run completes in a single ``ainvoke`` with no
    interrupt.
    """

    # ---- GROUND ----------------------------------------------------------
    async def ground(state: ClarionState) -> dict:
        """Retrieve goal-relevant fact(s) for this step via the Retriever port.
        Timestamps query-fire → first-fact for the §8 latency meter."""
        t0 = time.time()
        facts = await retriever.query(state["goal"])
        retrieval_ms = (time.time() - t0) * 1000.0
        return {
            "grounded_facts": list(facts),
            # Reducer channel: return ONLY the new event (operator.add concatenates).
            "trace": [
                _trace("GROUND", "exit", n_facts=len(facts), retrieval_ms=retrieval_ms)
            ],
        }

    # ---- VERIFY ----------------------------------------------------------
    def verify(state: ClarionState) -> dict:
        """The epistemic clause: mark only grounded facts verified. An ungrounded
        fact (``source_node_id is None``) is forced ``verified=False`` and can
        never be promoted to spoken — even negatives are first-class when sourced
        (execution §2.2 VERIFY)."""
        checked = assert_grounded(state["grounded_facts"])
        n_verified = sum(1 for f in checked if f.verified)
        n_refused = len(checked) - n_verified
        return {
            "grounded_facts": checked,
            "trace": [_trace("VERIFY", "exit", verified=n_verified, refused=n_refused)],
        }

    # ---- PROPOSE ---------------------------------------------------------
    def propose(state: ClarionState) -> dict:
        """Form the next spoken action from the verified facts + the current step.

        Deterministic for the kernel layer (a model planner drops in here later,
        per ST1): if there is a fillable textbox and a speakable fact, propose to
        fill it; otherwise propose a read-back. ``irreversible`` is carried from
        the chosen action so the mode gate and policy can read it.
        """
        page = state["page_index"]
        sayable = speakable(state["grounded_facts"])
        k, n = state["step"]
        proposal_id = f"prop-{state['stage_idx']}-{k}"

        # Pick the first interactive textbox to fill, if any.
        target_idx: Optional[int] = None
        for idx, node in page.nodes.items():
            if node.role in ("textbox", "searchbox"):
                target_idx = idx
                break

        # An irreversible control (submit / pay / confirm button) is the §5
        # hard-stop trigger — naming-based detection at the kernel layer; ST1
        # refines it with per-stage tool subsets.
        submit_idx: Optional[int] = None
        for idx, node in page.nodes.items():
            if node.role == "button" and any(
                w in node.name.lower() for w in ("pay", "submit", "confirm", "send")
            ):
                submit_idx = idx
                break

        if target_idx is not None and sayable:
            fact = sayable[0]
            node = page.nodes[target_idx]
            action = Action(
                kind="fill",
                index=target_idx,
                value=fact.value,
                irreversible=False,
            )
            utterance = (
                f"I found the {node.name or 'field'}. I'll fill it with "
                f"{fact.value}. Say yes to continue."
            )
        elif submit_idx is not None:
            # The irreversible step: click a submit/pay control. Always gated.
            node = page.nodes[submit_idx]
            action = Action(kind="click", index=submit_idx, irreversible=True)
            utterance = (
                f"I'm about to press {node.name}. This cannot be undone. "
                f"Say yes to confirm."
            )
        else:
            # Nothing to fill or submit → a read-back proposal (reversible).
            action = Action(kind="read", index=target_idx, irreversible=False)
            facts_str = ", ".join(f.value for f in sayable) or "no grounded facts yet"
            utterance = f"Here is what I found: {facts_str}."

        proposal = Proposal(
            id=proposal_id,
            utterance=utterance,
            action=action,
            irreversible=action.irreversible,
        )
        return {
            "pending_proposal": proposal,
            "trace": [
                _trace(
                    "PROPOSE",
                    "exit",
                    proposal_id=proposal.id,
                    irreversible=proposal.irreversible,
                )
            ],
        }

    # ---- mode gate -------------------------------------------------------
    def consent_gate(
        state: ClarionState,
    ) -> Literal["consent", "act"]:
        """The ``mode``-conditional edge (execution §2.3 / §3 / foundation §5).

        Normal → always route through CONSENT (every consequential step interrupts).
        Fast   → auto-proceed (skip straight to ACT) on a reversible proposal, but
                 ALWAYS route through CONSENT when the proposal is irreversible.

        ``mode`` is closed over from ``build_kernel`` so the compiled graph's
        behaviour is fixed; the proposal's own ``irreversible`` flag is the gate.
        """
        proposal = state["pending_proposal"]
        # No proposal (degenerate) → nothing to consent to; go act (a no-op).
        if proposal is None:
            return "act"
        if mode == "fast" and not proposal.irreversible:
            return "act"
        return "consent"

    # ---- ⟨CONSENT⟩ -------------------------------------------------------
    def consent(state: ClarionState) -> dict:
        """Surface the ``ConsentRequest`` and pause via ``interrupt()``.

        On ``Command(resume=ConsentDecision(...))`` this node re-executes from the
        top and ``interrupt()`` returns the decision payload. The node itself has
        no side-effect beyond appending to the consent_log (idempotent at the
        actuator level — the real side-effect lives in ACT, which is guarded)."""
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
            # Reducer channel: return ONLY the new Consent (operator.add appends).
            # This entry is also the agentic clause's record that ACT checks before
            # side-effecting (§2.3).
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

    # ---- ACT (IDEMPOTENT) ------------------------------------------------
    async def act(state: ClarionState) -> dict:
        """Execute the approved action via the Actuator — IDEMPOTENTLY (§2.3).

        Three guards, in order:
          1. Once-flag: if an ``acted_proposal_id`` marker for this proposal is
             already in the trace, do NOTHING (a re-entry on a second
             resume(approve) must not double-act).
          2. Consent: in Fast mode a reversible proposal arrives here WITHOUT
             routing through CONSENT, so it has no consent_log entry — that is
             fine (the agentic clause only gates irreversible acts). For everything
             that went through CONSENT, require an ``approve``; a reject/edit/
             respond decision means we do not act.
          3. Policy: ``assert_consented`` hard-stops an irreversible act that
             somehow reached here without an approved consent (defence in depth).
        """
        proposal = state["pending_proposal"]
        assert proposal is not None and proposal.action is not None

        # (1) idempotency once-flag
        if _already_acted(state, proposal.id):
            return {
                "trace": [
                    _trace(
                        "ACT",
                        "info",
                        skipped="already-acted",
                        acted_proposal_id=proposal.id,
                    )
                ]
            }

        # (2) consent: a proposal that went through CONSENT must be approved.
        went_through_consent = any(
            c.proposal_id == proposal.id for c in state["consent_log"]
        )
        if went_through_consent and not is_consented(proposal, state["consent_log"]):
            return {"trace": [_trace("ACT", "info", skipped="not-approved")]}

        # (3) policy hard-stop (agentic clause) — raises on an unconsented
        # irreversible act; passes for reversible or approved-irreversible.
        assert_consented(proposal, state["consent_log"])

        obs = await actuator.act(proposal.action)
        return {
            "page_index": obs.selector_map,
            "trace": [
                # The once-flag marker — its presence in the reducer-accumulated
                # trace makes a re-entry on resume idempotent.
                _trace(
                    "ACT",
                    "info",
                    acted_proposal_id=proposal.id,
                    success=obs.success,
                ),
                _trace("ACT", "exit", success=obs.success),
            ],
        }

    # ---- CONFIRM ---------------------------------------------------------
    async def confirm(state: ClarionState) -> dict:
        """Re-perceive and record (the done-predicate / silent-fail substrate
        ST1 builds on, execution §2.2 CONFIRM / §3.3)."""
        sm = await actuator.perceive()
        return {
            "page_index": sm,
            "trace": [_trace("CONFIRM", "exit", nodes=len(sm.nodes))],
        }

    builder = StateGraph(ClarionState)
    builder.add_node("ground", ground)
    builder.add_node("verify", verify)
    builder.add_node("propose", propose)
    builder.add_node("consent", consent)
    builder.add_node("act", act)
    builder.add_node("confirm", confirm)

    builder.add_edge(START, "ground")
    builder.add_edge("ground", "verify")
    builder.add_edge("verify", "propose")
    # The mode-conditional edge: Normal → consent; Fast → act (unless irreversible).
    builder.add_conditional_edges(
        "propose",
        consent_gate,
        {"consent": "consent", "act": "act"},
    )
    builder.add_edge("consent", "act")
    builder.add_edge("act", "confirm")
    builder.add_edge("confirm", END)

    return builder.compile(checkpointer=make_checkpointer())


def seed_state(
    goal: str = "pay my electric bill",
    mode: Mode = "normal",
) -> ClarionState:
    """A minimal valid ``ClarionState`` to start the kernel. ``page_index`` is
    empty; GROUND populates facts and the caller seeds ``page_index`` (or PROPOSE
    falls back to a read-back). The ``mode`` field mirrors the compiled graph's
    mode for downstream consumers (the routing is baked at ``build_kernel``)."""
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


__all__ = ["build_kernel", "seed_state", "make_checkpointer", "Mode"]
