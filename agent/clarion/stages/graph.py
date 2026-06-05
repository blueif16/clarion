"""ST1 → the GENERIC EXECUTOR (architecture migration Step 3): the goal-derived
plan + the kernel loop per subgoal, over the shared state.

**Not a baked AUTH→…→CONFIRM topology.** The hardcoded 6-stage pay-plan is
DELETED. The planner asks the injected ``Reasoner`` for a goal-derived
``list[Subgoal]`` (from the goal + the ORIENT readout + the page affordances);
the single generic ``executor`` node runs the K1 kernel loop scoped to the
current subgoal over the SHARED ``ClarionState``, then advances on the GENERIC
done-check — the reasoner-SELECTED ``success_check`` evaluated in CODE by
``stages.checks.evaluate_success_check`` (killer-closer #3, never model say-so).

KEPT verbatim: the RESCUE cross-cut (``detect_rescue`` — the most-validated
trigger), the bounded ``replanner``, and the §18.7 content-keyed reducer dedup
for the kernel sub-loop that re-executes across a consent interrupt.

Built ON K1 (imports the kernel + policy read-only; never edits them) and the
FROZEN ``clarion.contracts``. Private control-flow channels live on the
``_PlanState`` superset (the kernel's, extended) so ``contracts/`` stays frozen.

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

from clarion.actuator.pipeline import readout_from_selector_map
from clarion.contracts.events import ConsentDecision
from clarion.contracts.ports import Actuator, Reasoner, Retriever
from clarion.contracts.state import (
    ClarionState,
    Consent,
    Fact,
    PageReadout,
    PairedFact,
    SelectorMap,
    Subgoal,
    TraceEvent,
)
from clarion.kernel.graph import _ALLOWED_MSGPACK_MODULES, _PlanState, build_kernel, seed_state
from clarion.stages.checks import evaluate_success_check
from clarion.stages.planner import plan_goal, verbalize_subgoals
from clarion.stages.predicates import detect_rescue, needs_rescue


# ---------------------------------------------------------------------------
# Private control-flow schema — the kernel's _PlanState superset, plus this
# graph's leading-underscore control-flow channels (contracts stay frozen).
# ---------------------------------------------------------------------------


class _StageState(_PlanState, total=False):
    """The executor graph's runtime schema: the kernel ``_PlanState`` superset
    (frozen ``ClarionState`` + the additive plan/reasoner keys) PLUS this graph's
    private, leading-underscore control-flow channels (rescue-return target,
    replan bookkeeping, the before-map anchor for the done-check). ``total=False``
    so a bare ``ClarionState`` seed is still valid input."""

    # The executor node to return to after the rescue sub-flow completes.
    _rescue_return: Optional[str]
    # The subgoal index rescue last resolved for → won't re-trigger on same tree.
    _rescue_done_for: Optional[int]
    # How many replans we've spent on the current subgoal (bounded).
    _replan_attempts: int
    # Per-subgoal inner-kernel thread ids (subgoal idx → kernel thread_id), so a
    # consent interrupt surfaced through the parent resumes on the SAME inner
    # thread after the parent resumes.
    _kernel_threads: dict[str, str]
    # The SelectorMap BEFORE the current subgoal's kernel ran (the done-check diff
    # baseline) and the URL/anchor at that point.
    _before_map: Optional[SelectorMap]
    _anchor: Optional[str]


_PLANNER = "planner"
_EXECUTOR = "executor"
_RESCUE = "rescue"
_REPLANNER = "replanner"


def _trace(node: str, event: str = "info", **data: object) -> TraceEvent:
    return TraceEvent(node=node, event=event, at=time.time(), data=dict(data))


def _trace_key(e: TraceEvent) -> tuple:
    """Content identity of a trace event, for idempotent forwarding (a re-executed
    node must not re-append an event the parent channel already holds)."""
    return (e.node, e.event, e.at, repr(sorted(e.data.items())))


def _consent_key(c: Consent) -> tuple:
    return (c.proposal_id, c.decision, c.value, c.at)


def make_stage_checkpointer() -> InMemorySaver:
    """Same contract-model allowlist as the kernel (execution §18.6) so the
    executor graph's checkpointed state round-trips warning-free."""
    return InMemorySaver(
        serde=JsonPlusSerializer(allowed_msgpack_modules=_ALLOWED_MSGPACK_MODULES)
    )


# ---------------------------------------------------------------------------
# ORIENT helpers — gather the readout + affordances + pairings the planner /
# kernel need, degrading gracefully on an actuator without the extra reads.
# ---------------------------------------------------------------------------


async def _orient(actuator: Actuator, sm: SelectorMap) -> PageReadout:
    """The ORIENT readout for the planner. Prefers the actuator's full-AXTree
    ``describe_page``; falls back to the interactive ``SelectorMap`` so a fake /
    cached transport still yields a grounded (if thinner) readout."""
    describe = getattr(actuator, "describe_page", None)
    if describe is not None:
        return await describe()
    return readout_from_selector_map(sm)


async def _paired_facts(actuator: Actuator) -> list[PairedFact]:
    """The geometric label↔value pairings THIS cycle (fence #3 supply). Empty for
    an actuator without ``read_paired_facts`` (the fakes) — the pairing fence then
    simply has nothing to back an "X is Y" claim, which is the safe default."""
    read = getattr(actuator, "read_paired_facts", None)
    if read is None:
        return []
    return await read()


def build_stage_graph(
    reasoner: Reasoner,
    retriever: Retriever,
    actuator: Actuator,
    *,
    mode: Literal["normal", "fast"] = "normal",
    max_replans: int = 2,
):
    """Compile the GENERIC EXECUTOR graph.

    Topology::

        START → planner → executor ⇄ (replanner) → END
                            │
                            └→ rescue → back to executor

    - ``planner``  — asks the ``Reasoner`` for a goal-derived ``list[Subgoal]``
                     (from goal + ORIENT readout + affordances), speaks the plan
                     (legibility), routes to the executor at subgoal 0.
    - ``executor`` — runs the K1 kernel loop scoped to the CURRENT subgoal over the
                     shared state (re-surfacing any consent interrupt through the
                     parent), then advances on the GENERIC done-check
                     (``evaluate_success_check`` against the re-perceived tree).
                     Advances subgoal idx on success; → replanner on failure.
    - ``rescue``   — KEPT: a choked-widget cross-cut (``detect_rescue``).
    - ``replanner``— KEPT: bounded retry of the current subgoal, then gives up.

    ``mode`` is threaded into the per-subgoal kernel so the dual-signal gate
    behaves per foundation §5. NO baked stage names anywhere — the plan is whatever
    the reasoner derives from the goal.
    """

    # One kernel, reused across subgoals (state lives in ClarionState).
    kernel = build_kernel(reasoner, retriever, actuator, mode=mode)

    # ---- planner ---------------------------------------------------------
    async def planner(state: _StageState) -> Command:
        """Derive the goal-derived plan and route to the executor at subgoal 0."""
        sm = state["page_index"]
        orient = await _orient(actuator, sm)
        subgoals = await plan_goal(
            reasoner, state["goal"], orient, list(orient.affordances)
        )
        return Command(
            update={
                "subgoals": subgoals,
                "stage_idx": 0,
                "trace": [
                    _trace(
                        "PLANNER",
                        "exit",
                        n_subgoals=len(subgoals),
                        plan=[s.description for s in subgoals],
                        utterance=verbalize_subgoals(subgoals),
                    )
                ],
            },
            goto=_EXECUTOR,
        )

    async def _drive_kernel(state: _StageState, subgoal: Subgoal, idx: int) -> dict:
        """Run the K1 kernel loop for ONE subgoal over the shared state, driving it
        to completion even across a consent interrupt (re-surfaced through the
        parent's own ``interrupt()`` so the voice plane / panel see the identical
        ``ConsentRequest``). The inner thread id persists in ``_kernel_threads`` so
        a parent resume reaches the same checkpoint."""
        threads = dict(state.get("_kernel_threads") or {})
        key = str(idx)
        thread_id = threads.get(key) or f"kernel-{idx}-{uuid.uuid4()}"
        threads[key] = thread_id
        kernel_cfg = {"configurable": {"thread_id": thread_id}}

        # Seed the kernel with the frozen + additive plan channels scoped to THIS
        # subgoal's sub-goal. Pass the live paired_facts (the fence #3 supply).
        seed: _PlanState = {
            "goal": subgoal.description or state["goal"],
            "mode": state["mode"],
            "plan": state.get("plan", []),
            "stage_idx": idx,
            "step": state["step"],
            "page_index": state["page_index"],
            "grounded_facts": state["grounded_facts"],
            "pending_proposal": state["pending_proposal"],
            "consent_log": list(state["consent_log"]),
            "trace": list(state["trace"]),
            "pending_step": state.get("pending_step"),
            "paired_facts": state.get("paired_facts", []),
        }  # type: ignore[assignment]

        result = await kernel.ainvoke(seed, kernel_cfg)
        for _ in range(8):
            if "__interrupt__" not in result:
                break
            (parent_intr,) = result["__interrupt__"]
            decision_payload = interrupt(parent_intr.value)
            decision = ConsentDecision.model_validate(decision_payload)
            result = await kernel.ainvoke(
                Command(resume=decision.model_dump()), kernel_cfg
            )
        result["_kernel_threads"] = threads  # type: ignore[index]
        return result

    # ---- executor --------------------------------------------------------
    async def executor(state: _StageState) -> Command:
        idx = int(state.get("stage_idx", 0) or 0)
        subgoals: list[Subgoal] = state.get("subgoals", []) or []
        if idx >= len(subgoals):
            return Command(goto=END)  # plan exhausted.
        subgoal = subgoals[idx]

        # (1) RESCUE cross-cut FIRST (KEPT): a choked widget in the tree we're
        #     about to act on → branch to rescue, come back here. Don't re-trigger
        #     if rescue just resolved for this subgoal.
        sm: SelectorMap = state["page_index"]
        if needs_rescue(sm) and state.get("_rescue_done_for") != idx:
            choked = detect_rescue(sm)
            return Command(
                update={
                    "_rescue_return": _EXECUTOR,
                    "_rescue_done_for": idx,
                    "trace": [
                        _trace(
                            "EXECUTOR",
                            "info",
                            subgoal=idx,
                            rescue_triggered=True,
                            choked_indices=[n.index for n in choked],
                        )
                    ],
                },
                goto=_RESCUE,
            )

        # Capture the before-map + harvest the live pairings for THIS cycle (the
        # done-check diff baseline + the fence #3 supply).
        before_map = sm
        paired = await _paired_facts(actuator)

        # (2) Run the kernel loop scoped to this subgoal over the shared state.
        merged = await _drive_kernel(
            {**state, "paired_facts": paired},  # type: ignore[arg-type]
            subgoal,
            idx,
        )

        # (3) GENERIC done-check (killer-closer #3): the reasoner-SELECTED
        #     success_check, evaluated in CODE against the re-perceived tree.
        fresh: SelectorMap = merged["page_index"]
        check_name = merged.get("success_check") or subgoal.done_check or ""
        anchor = state.get("_anchor")
        advanced = evaluate_success_check(
            check_name, merged, before_map, fresh, anchor  # type: ignore[arg-type]
        )

        # Forward ONLY the kernel's genuinely-NEW reducer-channel entries (§18.7),
        # content-keyed so a consent re-execution can't double-count.
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
            "pending_step": merged.get("pending_step"),
            "success_check": merged.get("success_check", ""),
            "paired_facts": paired,
            "_kernel_threads": merged.get("_kernel_threads", {}),
            "_before_map": before_map,
            "trace": kernel_new_trace
            + [
                _trace(
                    "EXECUTOR",
                    "exit",
                    subgoal=idx,
                    done=advanced,
                    success_check=check_name,
                )
            ],
        }
        if kernel_new_consent:
            update["consent_log"] = kernel_new_consent

        if advanced:
            update["stage_idx"] = idx + 1
            update["_replan_attempts"] = 0
            update["_rescue_done_for"] = None
            # Next subgoal (or END if this was the last).
            goto = _EXECUTOR if idx + 1 < len(subgoals) else END
            return Command(update=update, goto=goto)

        # Done-check failed → the replanner (bounded retry).
        return Command(update=update, goto=_REPLANNER)

    # ---- RESCUE sub-flow (KEPT verbatim) ---------------------------------
    async def rescue(state: _StageState) -> Command:
        """The choked-widget cross-cut (foundation §4 — the most-validated trigger).
        Re-perceive (a real impl relabels via the vision fallback) and return to the
        executor. ``_rescue_done_for`` stops an immediate re-trigger on the same
        tree."""
        fresh = await actuator.perceive()
        still_choked = detect_rescue(fresh)
        return Command(
            update={
                "page_index": fresh,
                "_rescue_return": None,
                "trace": [
                    _trace(
                        "RESCUE",
                        "exit",
                        returned_to=_EXECUTOR,
                        resolved=not still_choked,
                        remaining_choked=[n.index for n in still_choked],
                    )
                ],
            },
            goto=_EXECUTOR,
        )

    # ---- replanner (KEPT) ------------------------------------------------
    async def replanner(state: _StageState) -> Command:
        """Bounded retry of the CURRENT subgoal: re-perceive and re-run it up to
        ``max_replans`` times, then give up to END so a wedged page can't loop."""
        attempts = int(state.get("_replan_attempts", 0) or 0) + 1
        idx = int(state.get("stage_idx", 0) or 0)
        if attempts > max_replans:
            return Command(
                update={
                    "trace": [
                        _trace(
                            "REPLANNER",
                            "exit",
                            gave_up=True,
                            subgoal=idx,
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
                "trace": [
                    _trace("REPLANNER", "exit", retrying=idx, attempts=attempts)
                ],
            },
            goto=_EXECUTOR,
        )

    # ---- assemble --------------------------------------------------------
    builder = StateGraph(_StageState)
    builder.add_node(_PLANNER, planner)
    builder.add_node(_EXECUTOR, executor)
    builder.add_node(_RESCUE, rescue)
    builder.add_node(_REPLANNER, replanner)

    builder.add_edge(START, _PLANNER)
    # All routing out of planner / executor / rescue / replanner is via Command(goto).

    return builder.compile(checkpointer=make_stage_checkpointer())


def seed_stage_state(
    goal: str = "",
    mode: Literal["normal", "fast"] = "normal",
    page_index: Optional[SelectorMap] = None,
) -> ClarionState:
    """A minimal valid ``ClarionState`` to start the executor graph. Reuses the
    kernel's ``seed_state`` (single source of truth for the state shape) and lets
    the caller seed the initial ``page_index`` (the freshly perceived tree). The
    ``goal`` is the real (restated) user goal — never a baked task."""
    state = seed_state(goal=goal, mode=mode)
    if page_index is not None:
        state["page_index"] = page_index
    return state


__all__ = [
    "build_stage_graph",
    "seed_stage_state",
    "make_stage_checkpointer",
]
