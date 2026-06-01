"""ST1 — the stage graph (execution §3): specialized nodes over shared state.

The hero plan (AUTH → LOCATE → FILL → REVIEW → ⟨PAY⟩ → CONFIRM) walked by a single
context, machine-checkable done-predicates + negative checks (never model
say-so), the RESCUE cross-cut for screen-reader-choked widgets, and a replanner
path. Built ON the K1 kernel (imported read-only); imports only
``clarion.contracts`` + ``clarion.kernel`` — never a real provider SDK
(foundation §6 invariant)."""

from clarion.stages.graph import (
    build_stage_graph,
    make_stage_checkpointer,
    seed_stage_state,
)
from clarion.stages.planner import HERO_STAGE_IDS, plan_goal, verbalize_plan
from clarion.stages.predicates import (
    DONE_PREDICATES,
    NEGATIVE_CHECKS,
    detect_rescue,
    is_choked_widget,
    needs_rescue,
    resolve_done_predicate,
    resolve_negative_check,
    stage_advances,
)

__all__ = [
    "build_stage_graph",
    "make_stage_checkpointer",
    "seed_stage_state",
    "plan_goal",
    "verbalize_plan",
    "HERO_STAGE_IDS",
    "DONE_PREDICATES",
    "NEGATIVE_CHECKS",
    "detect_rescue",
    "is_choked_widget",
    "needs_rescue",
    "resolve_done_predicate",
    "resolve_negative_check",
    "stage_advances",
]
