"""The GENERIC EXECUTOR (architecture migration Step 3): the goal-derived plan +
the kernel loop per subgoal over the shared state. The hardcoded
AUTH→…→CONFIRM pay topology is DELETED — the plan is Reasoner-derived. The RESCUE
cross-cut + bounded replanner are KEPT. Built ON the K1 kernel (read-only);
imports only ``clarion.contracts`` + ``clarion.kernel`` — never a real provider
SDK (foundation §6 invariant)."""

from clarion.stages.checks import evaluate_success_check
from clarion.stages.graph import (
    build_stage_graph,
    make_stage_checkpointer,
    seed_stage_state,
)
from clarion.stages.planner import plan_goal, verbalize_subgoals
from clarion.stages.predicates import (
    detect_rescue,
    is_choked_widget,
    needs_rescue,
)

__all__ = [
    "build_stage_graph",
    "make_stage_checkpointer",
    "seed_stage_state",
    "plan_goal",
    "verbalize_subgoals",
    "evaluate_success_check",
    "detect_rescue",
    "is_choked_widget",
    "needs_rescue",
]
