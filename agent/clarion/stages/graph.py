"""ST1 — the stage graph (execution §3): specialized nodes over shared state.

**Not agents-per-stage.** ONE agent / ONE context walks a stage graph; each stage
is a *specialized node* (its own tool subset + system-prompt placeholder + the
kernel loop scoped to its job). Transitions are ``Command(goto=next_stage)``; a
``replanner`` path fires when a stage's done-predicate (machine check, never
model say-so) fails. A **RESCUE** cross-cut detects "screen-reader-choked"
widgets in the current ``SelectorMap`` and branches to a rescue sub-flow, then
returns to the interrupted stage. Single context over the shared ``ClarionState``
(execution §3 / §3.1).

Built ON K1 (imports the kernel + policy + predicates read-only; never edits
them) and the FROZEN ``clarion.contracts``. The §18.7 reducer rule is honoured
throughout: every node returns ONLY its NEW ``trace`` / ``consent_log`` entries —
LangGraph's ``operator.add`` reducer concatenates (returning prior+new would
double-count and break the §2.3 idempotency guard the kernel reads out of trace).

Private control-flow channels (rescue-return target, replan bookkeeping) are NOT
added to the frozen ``ClarionState`` (execution §18.5 freeze rule). They live on a
``_StageState`` TypedDict that *extends* ``ClarionState`` with leading-underscore
keys, used ONLY as this graph's schema. Every value object remains a contract
type; kernel/predicate calls still receive a valid ``ClarionState``. (LangGraph
1.2.2 silently DROPS state keys absent from the schema — verified — so the private
keys MUST be declared somewhere; declaring them on a superset schema keeps the
contract pure.)

langgraph 1.2.2 facts (Context7 /websites/langchain_oss_python_langgraph,
verified 2026-05-31):
  - ``Command(update={...}, goto="node")`` BOTH updates state AND routes — the
    replacement for a conditional edge; control flow is data, not a fixed edge, so
    a node may ``goto`` an earlier node (the replanner loop) or forward.
  - ``Command(goto=END)`` ends the graph from inside a node.
  - A state channel ``Annotated[list[X], operator.add]`` is append/reduce: node
    returns are CONCATENATED, never overwritten (the §18.7 trace/consent_log rule).
  - A node returning ``Command`` should carry a ``Command[Literal[...]]`` return
    annotation listing its goto targets (graph rendering/validation); the stage
    nodes are built in a closure, so we annotate ``-> Command`` and rely on the
    runtime goto (validated by the tests).

This module OWNS only ``clarion/stages/``; it imports kernel + contracts read-only.
"""

from __future__ import annotations

import time
import uuid
from typing import Literal, Optional

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.constants import END, START
from langgraph.graph import StateGraph
from langgraph.types import Command, interrupt

from clarion.contracts.events import ConsentDecision

from clarion.contracts.ports import Actuator, Retriever
from clarion.contracts.state import (
    ClarionState,
    Consent,
    SelectorMap,
    Stage,
    TraceEvent,
)
from clarion.kernel.graph import _ALLOWED_MSGPACK_MODULES, build_kernel, seed_state
from clarion.stages.planner import plan_goal, verbalize_plan
from clarion.stages.predicates import (
    detect_rescue,
    needs_rescue,
    stage_advances,
)


# ---------------------------------------------------------------------------
# Private control-flow schema (superset of ClarionState — contracts stay frozen)
# ---------------------------------------------------------------------------


class _StageState(ClarionState, total=False):
    """The stage graph's runtime schema: every FROZEN ``ClarionState`` channel
    plus this graph's private, leading-underscore control-flow channels.

    These are NOT contract fields (execution §18.5: contracts/ stays pure). They
    are last-value-wins scratch the router uses to know where rescue returns and
    which stage the replanner retries. ``total=False`` so they're optional — a
    bare ``ClarionState`` seed is still valid input.
    """

    # The stage node to return to after the rescue sub-flow completes.
    _rescue_return: Optional[str]
    # The stage node rescue last resolved for → that stage won't re-trigger rescue
    # on the same tree (prevents a rescue⇄stage loop).
    _rescue_done_for: Optional[str]
    # The stage node whose done-predicate failed → the replanner retries it.
    _failed_stage: Optional[str]
    # How many replans we've spent (bounded by ``max_replans``).
    _replan_attempts: int
    # Per-stage inner-kernel thread ids (stage node name → kernel thread_id), so a
    # consent interrupt surfaced through the parent can be resumed on the SAME
    # inner-kernel thread after the parent resumes (the seam: the stage runs the
    # kernel as a sub-loop; the kernel's interrupt re-surfaces through the parent).
    _kernel_threads: dict[str, str]


# Node names in the compiled graph.
_PLANNER = "planner"
_REPLANNER = "replanner"
_RESCUE = "rescue"


def _trace(node: str, event: str = "info", **data: object) -> TraceEvent:
    return TraceEvent(node=node, event=event, at=time.time(), data=dict(data))


def _trace_key(e: TraceEvent) -> tuple:
    """Content identity of a trace event, for idempotent forwarding (a re-executed
    stage node must not re-append an event the parent channel already holds). The
    kernel's ``at=time.time()`` makes each genuine event unique."""
    return (e.node, e.event, e.at, repr(sorted(e.data.items())))


def _consent_key(c: Consent) -> tuple:
    """Content identity of a consent record, for idempotent forwarding."""
    return (c.proposal_id, c.decision, c.value, c.at)


def _stage_node_name(stage_id: str) -> str:
    return f"stage_{stage_id.lower()}"


def make_stage_checkpointer() -> InMemorySaver:
    """Same contract-model allowlist as the kernel (execution §18.6) so the stage
    graph's checkpointed state round-trips warning-free."""
    return InMemorySaver(
        serde=JsonPlusSerializer(allowed_msgpack_modules=_ALLOWED_MSGPACK_MODULES)
    )


def build_stage_graph(
    retriever: Retriever,
    actuator: Actuator,
    *,
    mode: Literal["normal", "fast"] = "normal",
    max_replans: int = 2,
):
    """Compile the stage graph for the hero plan.

    Topology::

        START → planner → stage_auth → stage_locate → stage_fill
              → stage_review → stage_pay → stage_confirm → END

    with two cross-cut nodes any stage can route to:
      - ``rescue``    — entered when ``detect_rescue`` flags a choked widget in the
                        current tree; runs the rescue sub-flow then returns to the
                        stage that branched (``_rescue_return``).
      - ``replanner`` — entered when a stage's done-predicate (machine check) fails;
                        re-runs that stage's kernel loop (bounded by
                        ``max_replans``) before giving up to END.

    Each stage node runs the K1 kernel loop scoped to its job over the SHARED
    state (single context, no per-stage agent — execution §3), then evaluates its
    done-predicate against the freshly re-perceived tree. ``mode`` is threaded into
    the per-stage kernel so the consent gate behaves per foundation §5.
    """

    # One kernel, reused across stages (the loop is stateless across invocations —
    # state lives in ClarionState, which we pass in each time).
    kernel = build_kernel(retriever, actuator, mode=mode)

    plan = plan_goal("pay my electric bill")
    ordered_ids = [s.id for s in plan]
    # node name → the next stage's node name (or END for the last).
    next_node: dict[str, str] = {}
    for i, sid in enumerate(ordered_ids):
        nxt = _stage_node_name(ordered_ids[i + 1]) if i + 1 < len(ordered_ids) else END
        next_node[_stage_node_name(sid)] = nxt

    # ---- planner ---------------------------------------------------------
    def planner(state: _StageState) -> Command:
        """Emit the explicit plan (read aloud verbatim → legibility) and route to
        the first stage. Deterministic for the hero goal; the model-planner seam
        lives in ``planner.plan_goal``."""
        return Command(
            update={
                "plan": plan,
                "stage_idx": 0,
                "trace": [
                    _trace(
                        "PLANNER",
                        "exit",
                        n_stages=len(plan),
                        plan=list(ordered_ids),
                        utterance=verbalize_plan(plan),
                    )
                ],
            },
            goto=_stage_node_name(ordered_ids[0]),
        )

    async def _drive_kernel(state: _StageState, stage: Stage, my_node: str) -> dict:
        """Run the K1 kernel loop for one stage over the SHARED state, returning
        the merged kernel result — driving it to completion even across a consent
        interrupt.

        The kernel is a separately-compiled graph (we build ON it, never edit it),
        so its ``interrupt()`` does NOT auto-propagate to this parent graph: its
        ``ainvoke`` returns a result carrying ``__interrupt__`` instead of pausing
        us (verified, langgraph 1.2.2). The seam therefore RE-SURFACES that
        consent through the parent's own ``interrupt()`` — so the voice plane /
        panel see the identical ``ConsentRequest`` — then resumes the inner kernel
        on its OWN thread with the decision. The inner thread id is persisted in
        ``_kernel_threads`` so a parent resume reaches the same checkpoint.

        We seed the kernel with the FROZEN ClarionState channels (scoped to this
        stage's sub-goal); the caller forwards only the kernel's NEW trace/consent
        delta (the §18.7 reducer rule)."""
        threads = dict(state.get("_kernel_threads") or {})
        thread_id = threads.get(my_node) or f"kernel-{stage.id}-{uuid.uuid4()}"
        threads[my_node] = thread_id
        kernel_cfg = {"configurable": {"thread_id": thread_id}}

        # Pass only the FROZEN ClarionState channels (strip our private keys — the
        # kernel's schema is the contract, which would drop them anyway).
        seed: ClarionState = {
            "goal": stage.goal,  # scope the kernel to THIS stage's sub-goal
            "mode": state["mode"],
            "plan": state["plan"],
            "stage_idx": state["stage_idx"],
            "step": state["step"],
            "page_index": state["page_index"],
            "grounded_facts": state["grounded_facts"],
            "pending_proposal": state["pending_proposal"],
            "consent_log": list(state["consent_log"]),
            "trace": list(state["trace"]),
        }  # type: ignore[assignment]

        result = await kernel.ainvoke(seed, kernel_cfg)
        # Drive across any number of inner consent interrupts (bounded for safety).
        for _ in range(8):
            if "__interrupt__" not in result:
                break
            # Re-surface the kernel's consent through the PARENT interrupt so the
            # voice plane / panel pause on the same ConsentRequest. On parent
            # resume this stage node re-executes from the top, _drive_kernel runs
            # again, re-seeds the kernel (which is already parked at CONSENT on its
            # own thread), and resumes it with the decision below.
            (parent_intr,) = result["__interrupt__"]
            decision_payload = interrupt(parent_intr.value)
            # Normalize to the ConsentDecision shape the kernel's CONSENT expects.
            decision = ConsentDecision.model_validate(decision_payload)
            result = await kernel.ainvoke(
                Command(resume=decision.model_dump()), kernel_cfg
            )
        result["_kernel_threads"] = threads  # type: ignore[index]
        return result

    def _make_stage_node(stage: Stage):
        """Build the specialized node for one stage, closing over its machine
        'done' gate (done-predicate + negative checks) and its plan position."""
        my_node = _stage_node_name(stage.id)
        forward = next_node[my_node]
        stage_pos = ordered_ids.index(stage.id)

        async def stage_node(state: _StageState) -> Command:
            # (1) RESCUE cross-cut FIRST: if the tree we're about to act on chokes
            #     the screen reader, branch to rescue and come back here (execution
            #     §3 note). Don't re-trigger if rescue just resolved for us.
            sm: SelectorMap = state["page_index"]
            if needs_rescue(sm) and state.get("_rescue_done_for") != my_node:
                choked = detect_rescue(sm)
                return Command(
                    update={
                        "_rescue_return": my_node,
                        "trace": [
                            _trace(
                                stage.id,
                                "info",
                                rescue_triggered=True,
                                choked_indices=[n.index for n in choked],
                            )
                        ],
                    },
                    goto=_RESCUE,
                )

            # (2) Run the kernel loop scoped to this stage over the shared state,
            #     driving it across any consent interrupt (re-surfaced through the
            #     parent so the voice plane / panel pause on the same request).
            merged = await _drive_kernel(state, stage, my_node)

            # The kernel re-perceived in CONFIRM → evaluate the stage's machine
            # 'done' gate against that fresh tree (execution §3.3 — never say-so).
            fresh: SelectorMap = merged["page_index"]
            advanced = stage_advances(
                merged, fresh, stage.done_predicate, stage.negative_checks
            )

            # Forward ONLY the kernel's genuinely-NEW reducer-channel entries
            # (§18.7) — and do it IDEMPOTENTLY. A consent interrupt re-surfaced
            # through the parent makes THIS stage node re-execute from the top on
            # every parent resume; LangGraph then re-applies our reducer-delta each
            # time. A naive positional slice would re-forward the same kernel
            # entries on each re-run → the exact double-count §18.7 warns about.
            # So we diff by CONTENT against the parent's already-accumulated channel
            # (the kernel's ``at=time.time()`` stamps make each real event unique),
            # guaranteeing a re-execution can only ever add what is truly new.
            prior_trace_keys = {_trace_key(e) for e in state["trace"]}
            prior_consent_keys = {_consent_key(c) for c in state["consent_log"]}
            kernel_new_trace = [
                e for e in merged["trace"] if _trace_key(e) not in prior_trace_keys
            ]
            kernel_new_consent = [
                c
                for c in merged["consent_log"]
                if _consent_key(c) not in prior_consent_keys
            ]

            update: dict = {
                "page_index": merged["page_index"],
                "grounded_facts": merged["grounded_facts"],
                "pending_proposal": merged["pending_proposal"],
                "_kernel_threads": merged.get("_kernel_threads", {}),
                "trace": kernel_new_trace
                + [
                    _trace(
                        stage.id,
                        "exit",
                        done=advanced,
                        done_predicate=stage.done_predicate,
                    )
                ],
            }
            if kernel_new_consent:
                update["consent_log"] = kernel_new_consent

            if advanced:
                update["stage_idx"] = stage_pos + 1
                return Command(update=update, goto=forward)

            # Done-predicate failed → the replanner path (execution §3.1).
            update["_failed_stage"] = my_node
            return Command(update=update, goto=_REPLANNER)

        return stage_node

    # ---- RESCUE sub-flow -------------------------------------------------
    async def rescue(state: _StageState) -> Command:
        """The cross-cut rescue sub-flow (execution §3 note, foundation §4 — the
        most-validated trigger, Aira 62%). A choked widget (interactive role with
        an empty accessible name / focus-trap) was detected; we model the rescue
        as a re-perceive (a real impl relabels via the vision fallback / heuristics
        — execution §4.2) and return to the stage that branched.

        We mark ``_rescue_done_for`` so the returned-to stage does NOT immediately
        re-trigger rescue on the same tree (avoids a rescue⇄stage loop)."""
        return_to = state.get("_rescue_return") or _stage_node_name(ordered_ids[0])
        fresh = await actuator.perceive()
        still_choked = detect_rescue(fresh)
        return Command(
            update={
                "page_index": fresh,
                "_rescue_done_for": return_to,
                "_rescue_return": None,
                "trace": [
                    _trace(
                        "RESCUE",
                        "exit",
                        returned_to=return_to,
                        resolved=not still_choked,
                        remaining_choked=[n.index for n in still_choked],
                    )
                ],
            },
            goto=return_to,
        )

    # ---- replanner -------------------------------------------------------
    async def replanner(state: _StageState) -> Command:
        """Revise when a stage's done-predicate fails or the page surprises us
        (execution §3.1). Bounded retry: re-run the failed stage up to
        ``max_replans`` times (re-perceiving first), then give up to END so a
        wedged page cannot loop forever."""
        attempts = int(state.get("_replan_attempts", 0) or 0) + 1
        failed = state.get("_failed_stage") or _stage_node_name(ordered_ids[0])
        if attempts > max_replans:
            return Command(
                update={
                    "trace": [
                        _trace(
                            "REPLANNER",
                            "exit",
                            gave_up=True,
                            failed_stage=failed,
                            attempts=attempts,
                        )
                    ]
                },
                goto=END,
            )
        fresh = await actuator.perceive()
        return Command(
            update={
                "page_index": fresh,
                "_replan_attempts": attempts,
                "_failed_stage": None,
                "trace": [
                    _trace("REPLANNER", "exit", retrying=failed, attempts=attempts)
                ],
            },
            goto=failed,
        )

    # ---- assemble --------------------------------------------------------
    builder = StateGraph(_StageState)
    builder.add_node(_PLANNER, planner)
    builder.add_node(_RESCUE, rescue)
    builder.add_node(_REPLANNER, replanner)
    for stage in plan:
        builder.add_node(_stage_node_name(stage.id), _make_stage_node(stage))

    builder.add_edge(START, _PLANNER)
    # All routing out of planner / stages / rescue / replanner is via Command(goto).

    return builder.compile(checkpointer=make_stage_checkpointer())


def seed_stage_state(
    goal: str = "pay my electric bill",
    mode: Literal["normal", "fast"] = "normal",
    page_index: Optional[SelectorMap] = None,
) -> ClarionState:
    """A minimal valid ``ClarionState`` to start the stage graph. Reuses the
    kernel's ``seed_state`` (single source of truth for the state shape) and lets
    the caller seed the initial ``page_index`` (the freshly perceived tree)."""
    state = seed_state(goal=goal, mode=mode)
    if page_index is not None:
        state["page_index"] = page_index
    return state


__all__ = [
    "build_stage_graph",
    "seed_stage_state",
    "make_stage_checkpointer",
]
