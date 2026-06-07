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
from clarion.contracts.ports import Actuator, ContextRanker, Reasoner, Retriever
from clarion.contracts.state import (
    Action,
    ClarionState,
    Consent,
    DecideContext,
    Fact,
    PairedFact,
    Proposal,
    SelectorMap,
    StepProposal,
    Subgoal,
    TraceEvent,
)
from clarion.kernel.irreversibility import classify
from clarion.kernel.negative_verifier import verify_negative
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
    # Memory / knowledge-layer recall (the user-memory design): a recalled plan
    # hint + the consent reminder may ride the executor's private state across a
    # consent interrupt, so they round-trip through the checkpointer too.
    ("clarion.contracts.state", "WorkflowEpisode"),
    ("clarion.contracts.state", "ConsentRecord"),
    ("clarion.contracts.state", "Recall"),
]

Mode = Literal["normal", "fast"]


# The default top-K hint slice the Reasoner decides over (the latency trim — feed
# the ranked top-K candidates, NOT all ~48 live ids, then re-measure decide_ms).
_DEFAULT_TOPK = 12

# The node-count GATE for the semantic ContextRanker: only rank when the live map
# has at least this many nodes. Measured (MiniMax-M3, local-MiniLM embed): at ~41
# nodes the enum-shrink decode savings (~340ms) ≈ the embed cost (~335ms) → wash;
# the ranker is a clear WIN only on bigger pages. Below the gate we feed the full
# map (skip the embed) so the ranker is win-or-FREE, never a net loss.
_DEFAULT_RANK_MIN_NODES = 48

# Fast-mode cap: how many REVERSIBLE auto-acts the agent may chain before it MUST
# surface a spoken progress beat / consent (architecture migration Step 5 — "cap
# Fast to one reversible act before a spoken progress beat"). One. After one silent
# reversible act the next consequential step is routed through CONSENT even if it
# is reversible, so the blind user is never carried through a chain of silent
# mutations without a checkpoint. A pure read-back never counts (no side-effect).
_DEFAULT_FAST_ACT_CAP = 1


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
    # The user's confirmed VERBATIM intent for the whole task (NOT the per-subgoal
    # goal). Threaded from the stage so PROPOSE can build the rich DecideContext —
    # the loss of this is what made the reasoner read a label instead of acting.
    user_intent: str
    # The accumulated decided steps this run (the full trajectory the reasoner sees
    # as history). Last-value-wins — PROPOSE returns the full list each pass.
    step_history: list[StepProposal]
    # What happened on the prior loop / why the current subgoal is not done yet (the
    # replan signal; set by the stage executor, surfaced into DecideContext).
    last_outcome: str
    # Advisory recalled plan hint (knowledge layer), surfaced into DecideContext.
    recall_hint: str
    # The geometric label↔value pairings harvested THIS perceive cycle (the
    # pairing-correctness fence #3 supply). Last-value-wins (re-read each cycle).
    paired_facts: list[PairedFact]
    # The goal-derived generic plan (replaces the baked Stage topology).
    subgoals: list[Subgoal]
    # The reasoner-SELECTED success check for the current step (a SELECTION).
    success_check: str
    # The IrreversibilityGate's classification of the pending proposal.
    irreversibility: str
    # The Fast-mode reversible-auto-act counter (the Step-5 cap). Incremented by
    # ACT each time it performs a reversible act that did NOT route through
    # CONSENT; read by ``consent_gate`` to force a spoken progress beat after the
    # cap. A last-value-wins channel (NOT a reducer): ACT writes the running count.
    fast_acts: int


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
    """SUPERSEDED (no longer on the PROPOSE path): a meaning-based ContextRanker is
    the intended successor; this lexical pre-rank was removed from ``propose()`` so
    the LLM decides over the full live map. Kept defined for the future
    semantic-ranker revival — do NOT re-wire it onto the hot path.

    Build the top-K hint SUB-map the Reasoner decides over — the latency trim.

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


async def _page_readout(actuator: Actuator):
    """The FRESH live page at the decision moment (title/url/screen-reader summary)
    for the rich DecideContext. Prefers the actuator's ``describe_page``; degrades
    to ``None`` for a fake/replay transport without it. Best-effort — a blocked
    read never breaks the decision."""
    describe = getattr(actuator, "describe_page", None)
    if describe is None:
        return None
    try:
        return await describe()
    except Exception:  # noqa: BLE001 - context is best-effort; degrade, don't crash
        return None


async def _build_decide_context(
    state: "_PlanState", actuator: Actuator
) -> DecideContext:
    """Assemble the rich situational frame the step-decider reasons inside: the
    user's VERBATIM intent, the plan phase (subgoal i/N + its done-check), the whole
    plan, the FRESH live page, and what just happened. This is the de-hardcoded
    'most-informed agent' input — meaning, never a keyword table."""
    subgoals = list(state.get("subgoals") or [])
    idx = int(state.get("stage_idx", 0) or 0)
    cur = subgoals[idx] if 0 <= idx < len(subgoals) else None
    readout = await _page_readout(actuator)
    return DecideContext(
        user_intent=(state.get("user_intent") or state.get("goal") or ""),
        subgoal_index=idx,
        subgoal_total=max(len(subgoals), 1),
        subgoal_description=(cur.description if cur else state.get("goal", "")),
        subgoal_done_check=(
            (cur.done_check if cur else "") or state.get("success_check", "")
        ),
        plan=[s.description for s in subgoals],
        page_title=(readout.title if readout else ""),
        page_url=(readout.url if readout else ""),
        page_summary=(readout.summary if readout else ""),
        last_outcome=(state.get("last_outcome") or ""),
        recall_hint=(state.get("recall_hint") or ""),
    )


def build_kernel(
    reasoner: Reasoner,
    retriever: Retriever,
    actuator: Actuator,
    mode: Mode = "normal",
    *,
    top_k: int = _DEFAULT_TOPK,
    fast_act_cap: int = _DEFAULT_FAST_ACT_CAP,
    ranker: Optional["ContextRanker"] = None,
    rank_min_nodes: Optional[int] = None,
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

    # The ContextRanker node-count gate (resolved once; win-or-free).
    _rank_min = (
        rank_min_nodes if rank_min_nodes is not None else _DEFAULT_RANK_MIN_NODES
    )

    # ---- GROUND ----------------------------------------------------------
    async def ground(state: ClarionState) -> dict:
        """Retrieve goal-relevant fact(s) for this step via the Retriever port.
        Timestamps query-fire → first-fact for the §8 latency meter.

        Also RESETS the Step-5 Fast-cap counter: the silent reversible-act budget
        is PER KERNEL PASS (one GROUND→ACT→CONFIRM drive). A fresh ``ainvoke`` runs
        GROUND, so each driven turn / re-driven subgoal starts the budget clean —
        the spoken progress beat that resets it IS the planner/executor re-entry
        that re-grounds. (This also keeps the counter from leaking across the stage
        executor's per-subgoal kernel re-drives — the budget is local to a pass.)"""
        t0 = time.time()
        facts = await retriever.query(state["goal"])
        retrieval_ms = (time.time() - t0) * 1000.0
        return {
            "grounded_facts": list(facts),
            "fast_acts": 0,
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
        """Ask the Reasoner for the next grounded step over the FULL live map,
        validate it (``reasoner_guard``), and form the grounded ``Proposal``.

        The LLM decides (architecture Thesis); the kernel only ENFORCES. Pipeline:
          1. Call ``reasoner.decide_step(goal, page, facts, history)`` over the
             FULL live ``SelectorMap`` — the model is the SEMANTIC decider, so
             ``target_index`` may resolve to ANY live control, including a
             goal-relevant one the grounded facts never lexically mention. The old
             lexical ``_topk_slice`` pre-rank is REMOVED (it pruned ~46→12 by
             string-overlap and made unmentioned controls untargetable — a banned
             keyword heuristic). A meaning-based ``ContextRanker`` may reintroduce a
             trim later behind a port; that is NOT built here.
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
        # The FULL decided-step trajectory this run (the history the reasoner reasons
        # over), not just the last step — so a replan can see it already tried a read.
        history = list(state.get("step_history") or [])

        # The RICH decision context: the user's VERBATIM intent, the plan phase, the
        # FRESH live page, what just happened. The step-decider is the most
        # consequential agent in the loop, so it gets the most context.
        ctx = await _build_decide_context(state, actuator)

        # (1) Reasoner decides over the candidate slice. With a ``ContextRanker``
        # injected, that's the SEMANTIC top-K (smaller target_index enum → faster
        # constrained decode + less prefill); otherwise the FULL live map. The kernel
        # still validates/resolves against the full ``page`` below, so a sliced index
        # is a strict subset and always resolves back. Best-effort: a ranker hiccup
        # degrades to the full map, never breaks the decision.
        ranked = page
        if ranker is not None and len(page.nodes) >= _rank_min:
            try:
                ranked = await ranker.rank(
                    ctx.user_intent or state["goal"], page, sayable, top_k
                )
            except Exception:  # noqa: BLE001 — ranking is best-effort; never break the decision
                ranked = page

        step: StepProposal = await reasoner.decide_step(
            state["goal"], ranked, sayable, history, context=ctx
        )
        new_history = history + [step]

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
                "step_history": new_history,
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

        # (2b) ABSTAIN-AND-CLARIFY (the hero beat). The Reasoner self-reports its own
        # ambiguity: a non-empty ``alternatives`` means the goal plausibly matched
        # MORE THAN ONE distinct live control. Rather than guess at a consequential
        # target, the kernel emits a SAFE read-back-and-ask that NAMES the rival
        # controls (by their live AX node names) and asks which the user meant. This
        # is a ``read`` action — no side-effect — so it routes straight to the user
        # as a spoken question and never acts on an ambiguous target. Filtered
        # defensively to valid live indices that are not the chosen target.
        alts = [
            i
            for i in step.alternatives
            if i in page.nodes and i != step.target_index
        ]
        if step.action_kind in ("fill", "click", "navigate") and alts:
            # Gather candidate names from the LIVE map (chosen target first), capped
            # to ~3, skipping empties — these are read off real perceived nodes.
            names: list[str] = []
            chosen_node = (
                page.nodes.get(step.target_index)
                if step.target_index is not None
                else None
            )
            if chosen_node is not None and chosen_node.name.strip():
                names.append(chosen_node.name.strip())
            for i in alts:
                nm = page.nodes[i].name.strip()
                if nm:
                    names.append(nm)
                if len(names) >= 3:
                    break
            if len(names) >= 2:
                named = ", or ".join([", ".join(names[:-1]), names[-1]]) if len(
                    names
                ) > 2 else " or ".join(names)
                utterance = (
                    f"I can act on more than one thing that matches — {named}. "
                    f"Which did you mean?"
                )
            else:
                utterance = (
                    "I found more than one control that could match — which did "
                    "you mean?"
                )
            proposal = Proposal(
                id=proposal_id,
                utterance=utterance,
                action=Action(kind="read", index=None, irreversible=False),
                irreversible=False,
            )
            return {
                "pending_proposal": proposal,
                "pending_step": step,
                "step_history": new_history,
                "success_check": step.success_check,
                "trace": [
                    _trace(
                        "PROPOSE",
                        "info",
                        abstained="ambiguous",
                        alternatives=alts,
                        proposal_id=proposal_id,
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
            # --- honest-decline (NegativeVerifier, fence #5) ------------------
            # A spoken NEGATIVE ("no late fee") is permitted ONLY from a
            # closed-world search over grounded_facts finding no asserting node AND
            # coverage evidence (a grounded `absent`-polarity fact read off the
            # perceived region). Else DOWNGRADE TO A HEDGE — a charge rendered as an
            # image (invisible to the AXTree) must NEVER become a confident "no late
            # fee" (architecture migration Step 5 killer acceptance). The positive
            # read-back path is already fenced by membership (#2); only an asserted
            # negative routes through the verifier — and the polarity is the model's
            # OWN self-report (``asserts_absence``), never a lexical keyword list.
            negative_topic = step.say if (step.say and step.asserts_absence) else ""
            if negative_topic:
                verdict = verify_negative(negative_topic, facts)
                if not verdict.speak:
                    # Cannot prove the negative → hedge, never a confident negative.
                    proposal = Proposal(
                        id=proposal_id,
                        utterance=(
                            "I couldn't confirm that either way from what I can read "
                            "on this page, so I don't want to guess."
                        ),
                        action=Action(kind="read", index=None, irreversible=False),
                        irreversible=False,
                    )
                    return {
                        "pending_proposal": proposal,
                        "pending_step": step,
                        "step_history": new_history,
                        "success_check": step.success_check,
                        "trace": [
                            _trace(
                                "PROPOSE",
                                "info",
                                hedged=verdict.reason,
                                proposal_id=proposal_id,
                            ),
                            _trace(
                                "PROPOSE", "exit", proposal_id=proposal_id, irreversible=False
                            ),
                        ],
                    }
                # Covered negative: speak the grounded `absent` fact verbatim, sourced.
                say = negative_topic
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
            "step_history": new_history,
            "success_check": step.success_check,
            "trace": [
                _trace(
                    "PROPOSE",
                    "exit",
                    proposal_id=proposal.id,
                    action_kind=action.kind,
                    value_ref=step.value_ref,
                    decide_ms=getattr(reasoner, "last_decide_ms", None),
                    intent=ctx.user_intent,
                    phase=f"{ctx.subgoal_index + 1}/{ctx.subgoal_total}",
                    done_check=ctx.subgoal_done_check,
                    scratch=step.scratch_reasoning,
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
        """The ``mode``-conditional edge (foundation §5 / killer-closer #2 + the
        Step-5 Fast-cap).

        Normal → always route through CONSENT (every consequential step interrupts).
        Fast   → auto-proceed (skip straight to ACT) ONLY when the
                 IrreversibilityGate classified the step ``reversible`` AND the
                 reversible-auto-act cap has not been reached; an ``irreversible``
                 OR ``unknown`` step ALWAYS routes through CONSENT, and a reversible
                 step that would exceed ``fast_act_cap`` is ALSO routed through
                 CONSENT (forcing a spoken progress beat so the blind user is never
                 chained through silent mutations — architecture migration Step 5).

        Reads the gate's flag off the proposal (set authoritatively by
        ``irreversibility_gate``) and the running ``fast_acts`` counter; ``mode``
        and ``fast_act_cap`` are closed over from ``build_kernel``. A degenerate
        read-back (a ``read`` action) auto-proceeds — it has no side-effect to gate
        and does not consume the cap.
        """
        proposal = state["pending_proposal"]
        if proposal is None:
            return "act"
        # A pure read-back never gates (no side-effect) and never consumes the cap.
        if proposal.action is not None and proposal.action.kind == "read":
            return "act"
        if mode == "fast" and not proposal.irreversible:
            # The Step-5 cap: only auto-proceed if we are still under the budget of
            # silent reversible acts; otherwise force a spoken progress beat / yes.
            if state.get("fast_acts", 0) < fast_act_cap:
                return "act"
            return "consent"
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

        # Step-5 Fast-cap bookkeeping: a REVERSIBLE act that did NOT route through
        # CONSENT is a silent auto-act — count it so the next consequential step is
        # forced to a spoken progress beat. An approved (consented) act or a pure
        # read does not consume the silent-act budget; a consented act resets it
        # (the user just got a checkpoint). A ``read`` never reaches ACT's act()
        # with a side-effect, but guard against it anyway.
        is_read = proposal.action.kind == "read"
        silent_auto_act = (
            not went_through_consent and not proposal.irreversible and not is_read
        )
        out: dict = {
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
        if silent_auto_act:
            out["fast_acts"] = state.get("fast_acts", 0) + 1
        elif went_through_consent:
            # A consented checkpoint just happened → the spoken-beat budget resets.
            out["fast_acts"] = 0
        return out

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
