"""Wave-C — the GENERIC EXECUTOR spine smoke tests (FakeReasoner-driven).

These replace the quarantined legacy pay-topology tests (``test_stages.py``) with
network-free spine tests for the de-hardcoded executor:

  (1) the planner emits a GOAL-DERIVED plan (subgoals from the Reasoner, no baked
      stage names) and routes to the executor.
  (2) the executor runs the kernel loop per subgoal and ADVANCES on the generic
      ``evaluate_success_check`` (a real page-state check, not model say-so).
  (3) a consequential step GATES at consent in Normal mode (the agentic invariant
      survives the de-hardcoding) and acts exactly once after approve.
  (4) the RESCUE cross-cut still fires on a choked widget and returns to the
      executor (KEPT through the migration).
  (5) the generic done-check evaluator certifies / refuses against the tree.

Pure: FakeReasoner/FakeActuator from clarion.fakes; zero provider SDKs.
"""

from __future__ import annotations

import uuid

import pytest
from langgraph.types import Command

from clarion.contracts.events import ConsentDecision
from clarion.contracts.ports import Actuator
from clarion.contracts.state import (
    Action,
    AxNode,
    Fact,
    Observation,
    PageDiff,
    SelectorMap,
    StepProposal,
    Subgoal,
)
from clarion.fakes import FakeActuator, FakeReasoner, FakeRetriever
from clarion.stages.checks import evaluate_success_check
from clarion.stages.graph import build_stage_graph, seed_stage_state
from clarion.stages.planner import plan_goal, verbalize_subgoals


def _cfg() -> dict:
    return {"configurable": {"thread_id": str(uuid.uuid4())}}


# ---------------------------------------------------------------------------
# A tiny actuator: a fillable field + a button; fill populates, click confirms.
# ---------------------------------------------------------------------------


class _FormActuator(Actuator):
    def __init__(self, *, never_fills: bool = False) -> None:
        self.act_calls: list[Action] = []
        self._filled = False
        self._never_fills = never_fills

    def _map(self) -> SelectorMap:
        amount = "Amount: $42.00" if self._filled else ""
        return SelectorMap(
            nodes={
                0: AxNode(index=0, role="textbox", name=amount,
                          state={"required": True}, node_id="n-amount"),
                1: AxNode(index=1, role="button", name="Continue", node_id="n-c"),
            },
            token_estimate=24,
        )

    async def perceive(self) -> SelectorMap:
        return self._map()

    async def act(self, action: Action) -> Observation:
        self.act_calls.append(action)
        if action.kind == "fill" and not self._never_fills:
            self._filled = True
        return Observation(selector_map=self._map(), success=True)

    async def diff(self, before: SelectorMap, after: SelectorMap) -> PageDiff:
        return PageDiff()


def _page_facts_retriever() -> FakeRetriever:
    facts = [Fact(value="$42.00", source_node_id="n-amount", verified=True)]
    return FakeRetriever(corpus={"": facts, "amount": facts, "fill": facts})


class _FillReasoner:
    """Goal-derived plan: one subgoal. decide_step fills the first interactive
    index with the first grounded fact (resolving its live Fact.id at decide-time
    so the value_ref is valid), reversible, success_check=field_nonempty."""

    def __init__(self) -> None:
        self.last_decide_ms = None
        self.plan_calls: list[str] = []
        self.decide_calls: list[str] = []

    async def plan_goal(self, goal, orient, affordances):  # noqa: ANN001, ARG002
        self.plan_calls.append(goal)
        return [Subgoal(description="enter the amount", done_check="field_nonempty")]

    async def decide_step(self, goal, ranked_slice, facts, history):  # noqa: ANN001, ARG002
        self.decide_calls.append(goal)
        target = next(iter(sorted(ranked_slice.nodes)), None)
        return StepProposal(
            scratch_reasoning="fill the amount field",
            action_kind="fill",
            target_index=target,
            value_ref=facts[0].id if facts else None,
            irreversibility="reversible",
            success_check="field_nonempty",
            say=facts[0].value if facts else "",
        )


def _fill_then_done_reasoner() -> _FillReasoner:
    return _FillReasoner()


# ---------------------------------------------------------------------------
# (1) the planner emits a goal-derived plan (no baked stage names)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_planner_emits_goal_derived_subgoals() -> None:
    reasoner = FakeReasoner(
        subgoals=[
            Subgoal(description="find the amount due", done_check="confirmation_fact"),
            Subgoal(description="confirm the value", done_check="confirmation_fact"),
        ]
    )
    from clarion.contracts.state import PageReadout

    subgoals = await plan_goal(
        reasoner, "find my balance", PageReadout(title="Account"), []
    )
    assert [s.description for s in subgoals] == [
        "find the amount due",
        "confirm the value",
    ]
    # No baked AUTH/LOCATE/PAY names anywhere — it's derived from the goal.
    assert all("pay electric" not in s.description.lower() for s in subgoals)
    spoken = verbalize_subgoals(subgoals)
    assert spoken.lower().startswith("here's my plan")
    assert "find the amount due" in spoken.lower()


@pytest.mark.asyncio
async def test_planner_falls_open_to_generic_subgoal_on_empty() -> None:
    """An empty reasoner plan falls open to a single generic subgoal naming the
    goal — never a hardcoded topology."""
    from clarion.contracts.state import PageReadout

    subgoals = await plan_goal(
        FakeReasoner(subgoals=[]), "unsubscribe me", PageReadout(), []
    )
    assert len(subgoals) == 1
    assert subgoals[0].description == "unsubscribe me"


# ---------------------------------------------------------------------------
# (2) the executor runs the kernel loop and advances on the generic check
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_executor_advances_on_generic_check() -> None:
    reasoner = _fill_then_done_reasoner()
    actuator = _FormActuator()
    graph = build_stage_graph(
        reasoner, _page_facts_retriever(), actuator, mode="fast", max_replans=1
    )
    seed = seed_stage_state(
        goal="enter the amount", mode="fast", page_index=await actuator.perceive()
    )
    final = await graph.ainvoke(seed, _cfg())

    # The plan was goal-derived + spoken.
    assert any(e.node == "PLANNER" for e in final["trace"])
    assert final["subgoals"], "no subgoals on state"
    # The fill happened and the EXECUTOR advanced on field_nonempty (a real
    # page-state check against the re-perceived tree — not model say-so).
    assert any(a.kind == "fill" for a in actuator.act_calls)
    exec_exits = [e for e in final["trace"] if e.node == "EXECUTOR" and e.event == "exit"]
    assert exec_exits and exec_exits[-1].data["done"] is True
    assert exec_exits[-1].data["success_check"] == "field_nonempty"


@pytest.mark.asyncio
async def test_executor_replans_then_gives_up_on_stuck_subgoal() -> None:
    """A field that never fills → the generic check stays False → the executor
    routes to the replanner, which retries (bounded) then gives up to END."""
    reasoner = _fill_then_done_reasoner()
    actuator = _FormActuator(never_fills=True)
    graph = build_stage_graph(
        reasoner, _page_facts_retriever(), actuator, mode="fast", max_replans=1
    )
    seed = seed_stage_state(
        goal="enter the amount", mode="fast", page_index=await actuator.perceive()
    )
    final = await graph.ainvoke(seed, _cfg())

    exec_exits = [e for e in final["trace"] if e.node == "EXECUTOR" and e.event == "exit"]
    assert exec_exits and all(e.data["done"] is False for e in exec_exits)
    gave_up = [e for e in final["trace"] if e.node == "REPLANNER" and e.data.get("gave_up")]
    assert gave_up, "expected the replanner to give up after max_replans"


# ---------------------------------------------------------------------------
# (3) a consequential step gates at consent in Normal mode (agentic invariant)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_executor_gates_consequential_step_in_normal_mode() -> None:
    reasoner = _fill_then_done_reasoner()
    actuator = _FormActuator()
    graph = build_stage_graph(
        reasoner, _page_facts_retriever(), actuator, mode="normal", max_replans=1
    )
    config = _cfg()
    seed = seed_stage_state(
        goal="enter the amount", mode="normal", page_index=await actuator.perceive()
    )
    paused = await graph.ainvoke(seed, config)
    # Normal mode gates the consequential fill step (consent re-surfaced through
    # the parent executor's interrupt).
    assert "__interrupt__" in paused
    assert actuator.act_calls == []  # nothing acted before the yes

    final = paused
    approve = Command(resume=ConsentDecision(decision="approve").model_dump())
    for _ in range(8):
        if "__interrupt__" not in final:
            break
        final = await graph.ainvoke(approve, config)
    assert "__interrupt__" not in final
    assert any(a.kind == "fill" for a in actuator.act_calls)
    assert any(c.decision == "approve" for c in final["consent_log"])


# ---------------------------------------------------------------------------
# (4) the RESCUE cross-cut still fires + returns (KEPT through the migration)
# ---------------------------------------------------------------------------


class _ChokedThenClearActuator(Actuator):
    def __init__(self) -> None:
        self.act_calls: list[Action] = []

    @staticmethod
    def choked() -> SelectorMap:
        return SelectorMap(
            nodes={0: AxNode(index=0, role="textbox", name="", node_id="n-blind")},
            token_estimate=10,
        )

    def _clean(self) -> SelectorMap:
        return SelectorMap(
            nodes={0: AxNode(index=0, role="textbox", name="Amount", node_id="n-blind")},
            token_estimate=14,
        )

    async def perceive(self) -> SelectorMap:
        return self._clean()

    async def act(self, action: Action) -> Observation:
        self.act_calls.append(action)
        return Observation(selector_map=self._clean(), success=True)

    async def diff(self, before: SelectorMap, after: SelectorMap) -> PageDiff:
        return PageDiff()


@pytest.mark.asyncio
async def test_rescue_cross_cut_fires_and_returns_in_executor() -> None:
    reasoner = FakeReasoner(
        subgoals=[Subgoal(description="read the amount", done_check="confirmation_fact")]
    )
    actuator = _ChokedThenClearActuator()
    graph = build_stage_graph(
        reasoner, FakeRetriever(), actuator, mode="fast", max_replans=1
    )
    seed = seed_stage_state(
        goal="read the amount", mode="fast", page_index=actuator.choked()
    )
    final = await graph.ainvoke(seed, _cfg())

    triggered = [e for e in final["trace"] if e.data.get("rescue_triggered")]
    assert triggered, "RESCUE was not triggered on the choked tree"
    rescue_exit = [e for e in final["trace"] if e.node == "RESCUE" and e.event == "exit"]
    assert rescue_exit, "RESCUE sub-flow did not run"
    assert rescue_exit[-1].data["returned_to"] == "executor"
    assert rescue_exit[-1].data["resolved"] is True
    # The loop guard: rescue did not re-trigger on the same (now-clean) tree.
    assert len(triggered) == 1


# ---------------------------------------------------------------------------
# (5) the generic done-check evaluator (the AG-DONE seam) — works + fails closed
# ---------------------------------------------------------------------------


def test_evaluate_success_check_field_nonempty() -> None:
    before = SelectorMap(
        nodes={0: AxNode(index=0, role="textbox", name="", node_id="f")}
    )
    after = SelectorMap(
        nodes={0: AxNode(index=0, role="textbox", name="Amount: $42.00", node_id="f")}
    )
    state = seed_stage_state(goal="fill")
    assert evaluate_success_check("field_nonempty", state, before, after) is True
    assert evaluate_success_check("field_nonempty", state, after, after) is False


def test_evaluate_success_check_node_added_and_error_absent() -> None:
    before = SelectorMap(nodes={0: AxNode(index=0, role="button", name="Go", node_id="b")})
    after = SelectorMap(
        nodes={
            0: AxNode(index=0, role="button", name="Go", node_id="b"),
            1: AxNode(index=1, role="status", name="Confirmation #123", node_id="c"),
        }
    )
    state = seed_stage_state(goal="go")
    assert evaluate_success_check("node_added", state, before, after) is True
    assert evaluate_success_check("error_absent", state, before, after) is True
    err = SelectorMap(nodes={0: AxNode(index=0, role="alert", name="Error: invalid", node_id="e")})
    assert evaluate_success_check("error_absent", state, before, err) is False


def test_evaluate_success_check_unknown_name_fails_closed() -> None:
    sm = SelectorMap(nodes={0: AxNode(index=0, role="button", name="x", node_id="b")})
    state = seed_stage_state(goal="x")
    # An unregistered / empty check NEVER advances (no silent always-pass).
    assert evaluate_success_check("bogus_check", state, sm, sm) is False
    assert evaluate_success_check("", state, sm, sm) is False
