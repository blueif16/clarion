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
from typing import Literal, Optional, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.constants import END, START
from langgraph.graph import StateGraph
from langgraph.types import interrupt

from clarion.contracts.events import ConsentDecision, ConsentRequest
from clarion.contracts.ports import Actuator, Reasoner, Retriever
from clarion.contracts.state import (
    Action,
    ClarionState,
    Consent,
    Fact,
    PairedFact,
    Proposal,
    SelectorMap,
    StepProposal,
    Subgoal,
    TraceEvent,
)
from clarion.kernel.irreversibility import classify
from clarion.kernel.policy import (
    assert_consented,
    assert_grounded,
    is_consented,
    is_speakable_value,
    speakable,
)
from clarion.kernel.reasoner_guard import resolve_value_ref, validate_step_proposal

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
    # Reasoner I/O + pairing now enter checkpointed state (the de-hardcoding
    # migration): the pending StepProposal, the derived Subgoal plan, and the
    # PairedFact pairing fence all round-trip through the checkpointer.
    ("clarion.contracts.state", "StepProposal"),
    ("clarion.contracts.state", "Subgoal"),
    ("clarion.contracts.state", "PairedFact"),
]

Mode = Literal["normal", "fast"]


# The default top-K hint slice the Reasoner decides over (the latency trim — feed
# the ranked top-K candidates, NOT all ~48 live ids, then re-measure decide_ms).
_DEFAULT_TOPK = 12


class _PlanState(ClarionState, total=False):
    """The SUPERSET schema the de-hardcoded kernel + generic executor walk:
    every FROZEN ``ClarionState`` channel PLUS the additive plan/reasoner keys.

    The frozen ``ClarionState`` stays minimal (contracts/ is frozen — architecture
    Memory). These leading-underscore-free additive keys are NOT contract fields;
    they live here on a ``total=False`` superset (the existing ``_StageState``
    pattern) so a bare ``ClarionState`` seed is still valid input and LangGraph
    (which DROPS keys absent from the schema) preserves them. Raw AXTree/HTML is
    NEVER carried here — only node values, the lean pending StepProposal, the
    Subgoal plan, the paired-fact fence, and the reasoner's selected check +
    irreversibility classification.
    """

    # The Reasoner's pending next-step decision (validated, pre-Proposal).
    pending_step: Optional[StepProposal]
    # The geometric label↔value pairings harvested THIS perceive cycle (the
    # pairing-correctness fence #3 supply). Last-value-wins (re-read each cycle).
    paired_facts: list[PairedFact]
    # The goal-derived generic plan (replaces the baked Stage topology).
    subgoals: list[Subgoal]
    # The reasoner-SELECTED success check for the current step (a SELECTION).
    success_check: str
    # The IrreversibilityGate's classification of the pending proposal.
    irreversibility: str


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


def _topk_slice(page: SelectorMap, facts: list[Fact], top_k: int) -> SelectorMap:
    """Build the top-K hint SUB-map the Reasoner decides over — the latency trim.

    The Reasoner must NOT be fed all ~48 live ids (the 4.7s full-map baseline); we
    hand it the top-K most goal-relevant candidates as a real ``SelectorMap`` keyed
    by the SAME live indices, so a returned ``target_index`` resolves straight back
    into the live map (and ``reasoner_guard`` validates against the FULL live map
    too — the slice is a HINT, never authoritative).

    Ranking hint: an interactive control whose name token-overlaps a grounded fact
    value floats up; otherwise insertion order. Pure + cheap (no provider, no I/O).
    Always returns at least the first ``top_k`` interactive nodes so a click target
    is never pruned away.
    """
    nodes = page.nodes
    if len(nodes) <= top_k:
        return page
    fact_words: set[str] = set()
    for f in facts:
        fact_words.update(w for w in f.value.lower().split() if len(w) >= 3)

    def _rank(idx: int) -> tuple[int, int]:
        n = nodes[idx]
        name_low = n.name.lower()
        overlap = sum(1 for w in fact_words if w in name_low)
        interactive = 1 if n.role in _INTERACTIVE_ROLES else 0
        return (overlap, interactive)

    chosen = sorted(nodes, key=_rank, reverse=True)[:top_k]
    sub = {i: nodes[i] for i in sorted(chosen)}
    return SelectorMap(nodes=sub, token_estimate=page.token_estimate)


# Interactive roles that can be an action target (the slice prefers these).
_INTERACTIVE_ROLES = {
    "textbox", "searchbox", "combobox", "spinbutton", "textarea",
    "button", "link", "checkbox", "radio", "switch", "menuitem", "tab",
}


def build_kernel(
    reasoner: Reasoner,
    retriever: Retriever,
    actuator: Actuator,
    mode: Mode = "normal",
    *,
    top_k: int = _DEFAULT_TOPK,
):
    """Compile the GROUND→VERIFY→PROPOSE→IrreversibilityGate→⟨CONSENT⟩→ACT→CONFIRM
    kernel — the de-hardcoded spine (architecture Thesis: the LLM decides, the
    kernel acts + enforces).

    PROPOSE is now Reasoner-driven: it asks ``reasoner.decide_step`` over the
    top-K HINT slice (the latency trim), validates the returned ``StepProposal``
    with ``reasoner_guard`` (off-page index / dangling value_ref → discard), forms
    the grounded ``Proposal`` from a membership-fenced verbatim value, and carries
    the model's irreversibility judgement into the gate. The old textbox/submit
    name-matcher is DELETED — no site-specific keyword list anywhere.

    ``mode`` is baked into the compiled graph's consent routing via the
    IrreversibilityGate's classification:
      - ``normal``: every consequential step hits ⟨CONSENT⟩ and interrupts;
      - ``fast``: ``reversible`` auto-proceeds straight to ACT, but
        ``irreversible`` OR ``unknown`` ALWAYS interrupts (the foundation §5
        hard-stop, generalized to the dual-signal gate — killer-closer #2).

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

    # ---- PROPOSE (Reasoner-driven) --------------------------------------
    async def propose(state: _PlanState) -> dict:
        """Ask the Reasoner for the next grounded step over the top-K HINT slice,
        validate it (``reasoner_guard``), and form the grounded ``Proposal``.

        The LLM decides (architecture Thesis); the kernel only ENFORCES. Pipeline:
          1. Slice the live map to the top-K candidates (the latency trim) and
             call ``reasoner.decide_step(goal, slice, facts, history)``.
          2. Validate the ``StepProposal`` against the FULL live map + Fact ids
             (``validate_step_proposal``). An off-page index / dangling value_ref
             is discarded → a safe read-back, never acted on.
          3. The spoken/filled value is the membership-fenced VERBATIM grounded
             span (``resolve_value_ref`` → fence #2 ``is_speakable_value``); a
             ``say`` that is not a live grounded member is dropped (never spoken).
          4. Carry the model's irreversibility judgement + the SELECTED
             success_check onto state for the IrreversibilityGate / executor.

        NO site-specific keyword list — the textbox/submit name-matcher is gone.
        """
        page = state["page_index"]
        facts = list(state["grounded_facts"])
        sayable = speakable(facts)
        k, _n = state["step"]
        proposal_id = f"prop-{state['stage_idx']}-{k}"
        history = list(state.get("pending_step") and [state["pending_step"]] or [])

        # (1) Reasoner decides over the top-K hint slice.
        hint = _topk_slice(page, sayable, top_k)
        step: StepProposal = await reasoner.decide_step(
            state["goal"], hint, sayable, history
        )

        # (2) Code-side post-decode fence against the FULL live map + facts.
        verdict = validate_step_proposal(step, page, sayable)
        if not verdict.ok:
            # Discard the off-page proposal → a safe grounded read-back. Never act.
            facts_str = ", ".join(f.value for f in sayable) or "no grounded facts yet"
            proposal = Proposal(
                id=proposal_id,
                utterance=f"Here is what I found: {facts_str}.",
                action=Action(kind="read", index=None, irreversible=False),
                irreversible=False,
            )
            return {
                "pending_proposal": proposal,
                "pending_step": step,
                "success_check": step.success_check,
                "trace": [
                    _trace(
                        "PROPOSE", "info", rejected=verdict.reason, proposal_id=proposal_id
                    ),
                    _trace(
                        "PROPOSE", "exit", proposal_id=proposal_id, irreversible=False
                    ),
                ],
            }

        # (3) Membership-fenced verbatim value (fence #2). resolve_value_ref returns
        # the byte-identical grounded span the ref points at; a model say not in the
        # live grounded set is never spoken.
        resolved: Optional[Fact] = resolve_value_ref(step.value_ref, sayable)
        value: Optional[str] = None
        if resolved is not None and is_speakable_value(resolved.value, facts):
            value = resolved.value

        target_node = page.nodes.get(step.target_index) if step.target_index is not None else None
        node_name = target_node.name if target_node is not None else ""

        if step.action_kind == "fill" and target_node is not None and value is not None:
            action = Action(kind="fill", index=step.target_index, value=value)
            say = step.say if step.say else value
            utterance = (
                f"I found the {node_name or 'field'}. I'll fill it with {say}. "
                f"Say yes to continue."
            )
        elif step.action_kind in ("click", "navigate") and target_node is not None:
            action = Action(kind=step.action_kind, index=step.target_index)
            utterance = f"I'm about to use {node_name or 'this control'}. Say yes to continue."
        else:
            # read (or a value-less step that resolved to nothing): a grounded
            # read-back of the membership-fenced say / sayable facts.
            say = step.say if (step.say and is_speakable_value(step.say, facts)) else ""
            if not say:
                say = ", ".join(f.value for f in sayable) or "no grounded facts yet"
            action = Action(kind="read", index=step.target_index)
            utterance = f"Here is what I found: {say}."

        proposal = Proposal(
            id=proposal_id,
            utterance=utterance,
            action=action,
            # irreversible flag set authoritatively by the IrreversibilityGate next.
            irreversible=False,
        )
        return {
            "pending_proposal": proposal,
            "pending_step": step,
            "success_check": step.success_check,
            "trace": [
                _trace(
                    "PROPOSE",
                    "exit",
                    proposal_id=proposal.id,
                    action_kind=action.kind,
                    value_ref=step.value_ref,
                    decide_ms=getattr(reasoner, "last_decide_ms", None),
                )
            ],
        }

    # ---- IrreversibilityGate (dual-signal) -------------------------------
    def irreversibility_gate(state: _PlanState) -> dict:
        """Classify the pending proposal's reversibility via the dual-signal
        ``kernel.irreversibility.classify`` (killer-closer #2). The model's
        judgement (carried on ``pending_step.irreversibility``) is combined with
        the independent code structural pre-screen; the result sets the
        ``Proposal.irreversible`` flag the consent routing reads.

        ``unknown`` is treated as gating (it is NOT reversible), so it routes
        through CONSENT even in Fast mode (the kernel-side half of the
        UNKNOWN-gates-Fast invariant — AG-GATE hardens the classifier itself)."""
        proposal = state["pending_proposal"]
        step = state.get("pending_step")
        if proposal is None:
            return {"irreversibility": "reversible"}
        model_judgment = step.irreversibility if step is not None else "unknown"
        cls = classify(proposal, state["page_index"], model_judgment)  # type: ignore[arg-type]
        gated = proposal.model_copy(update={"irreversible": cls != "reversible"})
        return {
            "pending_proposal": gated,
            "irreversibility": cls,
            "trace": [
                _trace(
                    "GATE",
                    "exit",
                    proposal_id=proposal.id,
                    classification=cls,
                    gates=cls != "reversible",
                )
            ],
        }

    # ---- mode gate -------------------------------------------------------
    def consent_gate(
        state: _PlanState,
    ) -> Literal["consent", "act"]:
        """The ``mode``-conditional edge (foundation §5 / killer-closer #2).

        Normal → always route through CONSENT (every consequential step interrupts).
        Fast   → auto-proceed (skip straight to ACT) ONLY when the
                 IrreversibilityGate classified the step ``reversible``; an
                 ``irreversible`` OR ``unknown`` step ALWAYS routes through CONSENT.

        Reads the gate's flag off the proposal (set authoritatively by
        ``irreversibility_gate``); ``mode`` is closed over from ``build_kernel``.
        A degenerate read-back (a ``read`` action) auto-proceeds — it has no
        side-effect to gate.
        """
        proposal = state["pending_proposal"]
        if proposal is None:
            return "act"
        # A pure read-back never gates (no side-effect).
        if proposal.action is not None and proposal.action.kind == "read":
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

    builder = StateGraph(_PlanState)
    builder.add_node("ground", ground)
    builder.add_node("verify", verify)
    builder.add_node("propose", propose)
    builder.add_node("gate", irreversibility_gate)
    builder.add_node("consent", consent)
    builder.add_node("act", act)
    builder.add_node("confirm", confirm)

    builder.add_edge(START, "ground")
    builder.add_edge("ground", "verify")
    builder.add_edge("verify", "propose")
    # PROPOSE → IrreversibilityGate (classify) → the mode-conditional consent edge.
    builder.add_edge("propose", "gate")
    builder.add_conditional_edges(
        "gate",
        consent_gate,
        {"consent": "consent", "act": "act"},
    )
    builder.add_edge("consent", "act")
    builder.add_edge("act", "confirm")
    builder.add_edge("confirm", END)

    return builder.compile(checkpointer=make_checkpointer())


def seed_state(
    goal: str = "",
    mode: Mode = "normal",
) -> ClarionState:
    """A minimal valid ``ClarionState`` to start the kernel. ``goal`` is supplied
    by the caller (the real user/restated goal — NOT a baked task). ``page_index``
    is empty; GROUND populates facts and the caller seeds ``page_index`` (or
    PROPOSE forms a grounded read-back). The ``mode`` field mirrors the compiled
    graph's mode for downstream consumers (the routing is baked at
    ``build_kernel``)."""
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


__all__ = ["build_kernel", "seed_state", "make_checkpointer", "Mode", "_PlanState"]
