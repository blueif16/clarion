"""ST1 — the planner (execution §3.1 / §3.2).

The ``planner`` emits the explicit ``plan: list[Stage]`` — the thing the agent
reads aloud verbatim for instant legibility (execution §3.1). For the hero goal
("pay my electric bill") it is **deterministic**: the six §3.2 stages

    AUTH → LOCATE → FILL → REVIEW → ⟨PAY⟩ → CONFIRM

each carrying a registered ``done_predicate`` name + a ``negative_checks`` list
(per the §3.2 table). A model planner drops in later — ``plan_goal`` is the seam:
swap its body for an LLM call that emits the same ``list[Stage]`` shape and every
downstream consumer (the stage graph, the predicates registry) is unchanged.

Pure: ``clarion.contracts`` only. NO provider SDKs, NO langgraph.

Each ``Stage`` (execution §18.3) carries:
  - ``id`` / ``goal``      — the verbalizable name + sub-goal,
  - ``tools``              — the per-stage tool subset (execution §3.2 col 2),
  - ``done_predicate``     — a NAME into ``predicates.DONE_PREDICATES`` (never a
                             model say-so — §3.3),
  - ``negative_checks``    — NAMES into ``predicates.NEGATIVE_CHECKS``.
"""

from __future__ import annotations

from clarion.contracts.state import Stage

# The hero task's stage order (execution §3.2). ⟨PAY⟩ is the consent gate; the
# bracket in the doc is the "this is the irreversible step" marker.
HERO_STAGE_IDS: tuple[str, ...] = (
    "AUTH",
    "LOCATE",
    "FILL",
    "REVIEW",
    "PAY",
    "CONFIRM",
)


def _hero_plan() -> list[Stage]:
    """The deterministic §3.2 plan for "pay my electric bill". Every stage maps
    1:1 to a row of the §3.2 table: tool subset, done-predicate name, and the
    negative-verification list."""
    return [
        Stage(
            id="AUTH",
            goal="Log in to the utility account",
            tools=["navigate", "read", "fill"],
            done_predicate="auth_done",
            negative_checks=["no_error_banner"],
        ),
        Stage(
            id="LOCATE",
            goal="Find the amount, payee, and due date — all grounded",
            tools=["navigate", "read", "retrieve"],
            done_predicate="locate_done",
            negative_checks=["no_autopay_scheduled"],
        ),
        Stage(
            id="FILL",
            goal="Populate every required payment field",
            tools=["read", "fill"],
            done_predicate="fill_done",
            negative_checks=["no_required_field_blank", "no_silent_validation_error"],
        ),
        Stage(
            id="REVIEW",
            goal="Cross-check the amount and payee before paying",
            tools=["read", "retrieve"],
            done_predicate="review_done",
            negative_checks=["no_surprise_fee"],
        ),
        Stage(
            id="PAY",
            goal="Submit the payment (the consented, irreversible step)",
            tools=["submit"],
            done_predicate="pay_done",
            negative_checks=["confirmation_present"],
        ),
        Stage(
            id="CONFIRM",
            goal="Confirm success and read back the confirmation number",
            tools=["read", "write"],
            done_predicate="confirm_done",
            negative_checks=["not_still_on_form"],
        ),
    ]


def plan_goal(goal: str) -> list[Stage]:
    """Emit the ``plan: list[Stage]`` for ``goal``.

    DETERMINISTIC for the hero goal (execution §3.1 / hero task §3.2). This is the
    seam a model planner drops into later: a future implementation classifies the
    goal and emits a tailored ``list[Stage]``; for the hackathon hero run it
    always emits the six §3.2 stages. (Kept a free function, not a method, so the
    swap is a one-line body change with the same signature.)
    """
    # A model planner would branch on `goal` here; the hero path is fixed.
    return _hero_plan()


def verbalize_plan(plan: list[Stage]) -> str:
    """The read-aloud rendering of a plan (execution §3.1 "read aloud verbatim").
    One coherent sentence the agent can speak so the user hears the whole plan
    before any action — the legibility beat."""
    steps = ", then ".join(s.goal.lower() for s in plan)
    return f"Here's my plan: first {steps}."


__all__ = ["plan_goal", "verbalize_plan", "HERO_STAGE_IDS"]
