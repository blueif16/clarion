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

from clarion.contracts.ports import Reasoner
from clarion.contracts.state import Fact, PageReadout, Subgoal

__all__ = ["plan_goal", "verbalize_subgoals"]


async def plan_goal(
    reasoner: Reasoner,
    goal: str,
    orient: PageReadout,
    affordances: list[Fact],
) -> list[Subgoal]:
    """Derive a generic, site-agnostic plan from the goal + the ORIENT readout +
    the page affordances, via the injected ``Reasoner``. Replaces ``_hero_plan``.

    The plan is GOAL-DERIVED: the subgoals come from the model reasoning over the
    goal and what the page actually offers — there is ZERO site-specific code, no
    baked stage list. Fails-open to a single generic subgoal if the reasoner
    returns nothing, so the executor always has at least one loop to run."""
    subgoals = await reasoner.plan_goal(goal, orient, affordances)
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
