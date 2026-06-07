"""The planner — Reasoner-driven, goal-derived (architecture migration Step 3).

The hardcoded ``_hero_plan`` (the baked AUTH→…→CONFIRM "pay my electric bill"
topology) is DELETED. The plan is now derived by the frozen ``Reasoner`` port from
the goal + the ORIENT screen-reader readout + the page affordances:

    plan_goal(reasoner, goal, orient, affordances) -> [Subgoal]

Each ``Subgoal`` is generic and site-agnostic (no stage names, no "pay electric
bill"); its ``done_check`` names a registered generic success check (a SELECTION,
evaluated in CODE by ``stages.checks`` — never model say-so). The generic
executor (``stages.graph``) runs the kernel loop per subgoal.

Pure: ``clarion.contracts`` only. NO provider SDKs, NO langgraph (the Gemini SDK
lives in the injected ``Reasoner`` adapter, never here).
"""

from __future__ import annotations

from typing import Optional

from clarion.contracts.ports import Reasoner
from clarion.contracts.state import Fact, PageReadout, Subgoal, WorkflowEpisode

__all__ = ["plan_goal", "verbalize_subgoals"]


def _augment_goal_with_hint(goal: str, hint: WorkflowEpisode) -> str:
    """Fold a recalled prior plan into the goal text as ADVISORY context.

    This is how the user-memory reuse reaches the LLM WITHOUT changing the frozen
    ``Reasoner`` ABC: every adapter already consumes ``goal``, so the hint rides in
    there and the model re-grounds against the live page (it may discard the hint).
    The stored ``state['goal']`` is unchanged — only the per-call plan input is
    augmented."""
    steps = "; ".join(s.description for s in hint.subgoals if s.description)
    if not steps:
        return goal
    where = f" on {hint.url_host}" if hint.url_host else ""
    return (
        f"{goal}\n\n[Memory — a PRIOR working plan for a similar goal{where} was: "
        f"{steps}. Reuse the parts that still apply to the LIVE page; discard "
        f"anything that does not match what is actually here, and re-ground every "
        f"value on the live page.]"
    )


async def plan_goal(
    reasoner: Reasoner,
    goal: str,
    orient: PageReadout,
    affordances: list[Fact],
    *,
    prior_plan_hint: Optional[WorkflowEpisode] = None,
) -> list[Subgoal]:
    """Derive a generic, site-agnostic plan from the goal + the ORIENT readout +
    the page affordances, via the injected ``Reasoner``. Replaces ``_hero_plan``.

    The plan is GOAL-DERIVED: the subgoals come from the model reasoning over the
    goal and what the page actually offers — there is ZERO site-specific code, no
    baked stage list. ``prior_plan_hint`` (a recalled past episode) is folded into
    the goal text as advisory context to warm-start the plan; it is never binding —
    the model re-grounds against the live page. Fails-open to a single generic
    subgoal if the reasoner returns nothing, so the executor always has a loop."""
    effective_goal = goal
    if prior_plan_hint is not None and prior_plan_hint.subgoals:
        effective_goal = _augment_goal_with_hint(goal, prior_plan_hint)
    subgoals = await reasoner.plan_goal(effective_goal, orient, affordances)
    if not subgoals:
        # The reasoner declined / returned empty — fall back to one generic
        # subgoal naming the goal (still site-agnostic, still grounded execution).
        return [Subgoal(description=goal, done_check="")]
    return subgoals


def verbalize_subgoals(subgoals: list[Subgoal]) -> str:
    """The read-aloud rendering of a goal-derived plan (the legibility beat — the
    agent speaks the whole plan before any action). One coherent sentence over the
    generic subgoal descriptions."""
    if not subgoals:
        return "I don't have a plan yet."
    steps = ", then ".join(s.description.lower() for s in subgoals if s.description)
    return f"Here's my plan: first {steps}."
