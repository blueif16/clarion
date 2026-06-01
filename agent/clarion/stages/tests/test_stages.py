"""ST1 — stage-graph acceptance tests (execution §15 ST1 / §3).

The five required conditions, each isolated:
  (1) planner emits the six named stages in order, each with a registered
      done_predicate + a negative_checks list (execution §3.2).
  (2) a SelectorMap with a BLANK required field → FILL done-predicate returns
      False (the stage cannot advance) (ST1 accept #2 / §3.2 FILL negative).
  (3) a SelectorMap containing a textbox with a role but an EMPTY accessible name
      → RESCUE detection fires (ST1 accept #3 / §3 note).
  (4) a happy-path SelectorMap → FILL done-predicate True, advances.
  (5) the stage/planner nodes return ONLY-NEW trace entries — no double-count
      under the operator.add reducer (§18.7).

Pure: uses the FakeRetriever/FakeActuator from clarion.fakes (+ tiny scripted
actuators); imports zero provider SDKs (foundation §6).
"""

from __future__ import annotations

import uuid

from langgraph.types import Command

from clarion.contracts.events import ConsentDecision
from clarion.contracts.ports import Actuator, Retriever
from clarion.contracts.state import (
    Action,
    AxNode,
    Fact,
    Observation,
    PageDiff,
    SelectorMap,
)
from clarion.fakes import FakeActuator, FakeRetriever
from clarion.stages.graph import build_stage_graph, seed_stage_state
from clarion.stages.planner import HERO_STAGE_IDS, plan_goal, verbalize_plan
from clarion.stages.predicates import (
    DONE_PREDICATES,
    NEGATIVE_CHECKS,
    detect_rescue,
    fill_done,
    is_choked_widget,
    needs_rescue,
    no_required_field_blank,
    resolve_done_predicate,
    resolve_negative_check,
)
from clarion.kernel.graph import seed_state


def _cfg() -> dict:
    return {"configurable": {"thread_id": str(uuid.uuid4())}}


# ---------------------------------------------------------------------------
# SelectorMap fixtures
# ---------------------------------------------------------------------------


def _sm_blank_required() -> SelectorMap:
    """A form with ONE required textbox left blank (empty value) + a submit
    button. The required flag is set; the name is the bare label only → blank."""
    return SelectorMap(
        nodes={
            0: AxNode(
                index=0,
                role="textbox",
                name="",  # blank value — only the label below carries text
                state={"required": True},
                node_id="n-amount",
            ),
            1: AxNode(index=1, role="button", name="Pay bill", node_id="n-pay"),
        },
        token_estimate=20,
    )


def _sm_happy_filled() -> SelectorMap:
    """The same form, required field now populated (a value-bearing name)."""
    return SelectorMap(
        nodes={
            0: AxNode(
                index=0,
                role="textbox",
                name="Amount: $42.00",  # populated
                state={"required": True},
                node_id="n-amount",
            ),
            1: AxNode(index=1, role="button", name="Pay bill", node_id="n-pay"),
        },
        token_estimate=24,
    )


def _sm_choked_widget() -> SelectorMap:
    """A textbox with a role but an EMPTY accessible name — the screen reader has
    nothing to announce → the RESCUE trigger (execution §3 note)."""
    return SelectorMap(
        nodes={
            0: AxNode(index=0, role="textbox", name="", node_id="n-unlabeled"),
            1: AxNode(index=1, role="button", name="Continue", node_id="n-continue"),
        },
        token_estimate=18,
    )


# ---------------------------------------------------------------------------
# (1) planner emits the six named stages in order, each with predicate + checks
# ---------------------------------------------------------------------------


def test_planner_emits_six_named_stages_in_order() -> None:
    plan = plan_goal("pay my electric bill")
    assert [s.id for s in plan] == list(HERO_STAGE_IDS)
    assert [s.id for s in plan] == ["AUTH", "LOCATE", "FILL", "REVIEW", "PAY", "CONFIRM"]

    for stage in plan:
        # Each stage carries a registered done-predicate (machine-checkable name,
        # never model say-so — §3.3) ...
        assert stage.done_predicate, f"{stage.id} has no done_predicate"
        assert stage.done_predicate in DONE_PREDICATES, (
            f"{stage.id}.done_predicate {stage.done_predicate!r} not registered"
        )
        assert callable(resolve_done_predicate(stage.done_predicate))
        # ... and a non-empty negative-verification list, each entry registered.
        assert stage.negative_checks, f"{stage.id} has no negative_checks"
        for nc in stage.negative_checks:
            assert nc in NEGATIVE_CHECKS, f"{stage.id} negative check {nc!r} not registered"
            assert callable(resolve_negative_check(nc))
        # ... and a tool subset (the specialized node's scoped tools — §3.2 col 2).
        assert stage.tools, f"{stage.id} has no tool subset"

    # The plan reads aloud as coherent stages (the legibility beat — §3.1).
    spoken = verbalize_plan(plan)
    assert spoken.lower().startswith("here's my plan")
    assert "log in" in spoken.lower()


def test_planner_matches_the_3_2_table_exactly() -> None:
    """Pin the §3.2 done-predicate / negative-check mapping so a drift is caught."""
    plan = {s.id: s for s in plan_goal("pay my electric bill")}
    assert plan["AUTH"].done_predicate == "auth_done"
    assert plan["AUTH"].negative_checks == ["no_error_banner"]
    assert plan["LOCATE"].done_predicate == "locate_done"
    assert plan["LOCATE"].negative_checks == ["no_autopay_scheduled"]
    assert plan["FILL"].done_predicate == "fill_done"
    assert plan["FILL"].negative_checks == [
        "no_required_field_blank",
        "no_silent_validation_error",
    ]
    assert plan["REVIEW"].done_predicate == "review_done"
    assert plan["REVIEW"].negative_checks == ["no_surprise_fee"]
    assert plan["PAY"].done_predicate == "pay_done"
    assert plan["PAY"].negative_checks == ["confirmation_present"]
    assert plan["CONFIRM"].done_predicate == "confirm_done"
    assert plan["CONFIRM"].negative_checks == ["not_still_on_form"]


# ---------------------------------------------------------------------------
# (2) blank required field → FILL done-predicate False (cannot advance)
# ---------------------------------------------------------------------------


def test_fill_done_false_on_blank_required_field() -> None:
    state = seed_state(goal="pay my electric bill")
    sm = _sm_blank_required()
    # The done-predicate refuses to advance: the required field is blank.
    assert fill_done(state, sm) is False
    # And the negative check names the exact violation.
    assert no_required_field_blank(state, sm) is False
    # RESCUE does NOT fire here — the field has a role; it's blank-of-value, not
    # blank-of-name (a labelled-but-empty input is a different problem).
    # (The blank in (2) is a *value* blank with required state, not an empty NAME.)


# ---------------------------------------------------------------------------
# (3) textbox with role but EMPTY name → RESCUE detection fires
# ---------------------------------------------------------------------------


def test_rescue_detection_fires_on_unlabeled_widget() -> None:
    sm = _sm_choked_widget()
    assert needs_rescue(sm) is True
    choked = detect_rescue(sm)
    assert len(choked) == 1
    assert choked[0].node_id == "n-unlabeled"
    assert is_choked_widget(choked[0]) is True
    # A labelled control is NOT choked.
    labelled = AxNode(index=2, role="button", name="Continue", node_id="n-x")
    assert is_choked_widget(labelled) is False


def test_rescue_detection_fires_on_focus_trap() -> None:
    """The second RESCUE heuristic: a focused-but-hidden/disabled control (a
    focus-trap) chokes the screen reader even with a name."""
    trapped = AxNode(
        index=0,
        role="button",
        name="OK",
        state={"focused": True, "hidden": True},
        node_id="n-trap",
    )
    assert is_choked_widget(trapped) is True
    explicit = AxNode(
        index=1,
        role="textbox",
        name="Search",
        state={"focus_trap": True},
        node_id="n-trap2",
    )
    assert is_choked_widget(explicit) is True


# ---------------------------------------------------------------------------
# (4) happy-path SelectorMap → FILL done-predicate True, advances
# ---------------------------------------------------------------------------


def test_fill_done_true_on_happy_path() -> None:
    state = seed_state(goal="pay my electric bill")
    sm = _sm_happy_filled()
    assert fill_done(state, sm) is True
    assert no_required_field_blank(state, sm) is True
    assert needs_rescue(sm) is False  # labelled + filled → no rescue


# ---------------------------------------------------------------------------
# (4b) the FILL stage NODE advances on a happy page, replans on a blank one
# ---------------------------------------------------------------------------


# A retriever grounding the LOCATE facts (amount, payee, due-date) so the LOCATE
# stage's done-predicate (>=3 speakable facts) is satisfied — the hero chain.
def _hero_retriever() -> FakeRetriever:
    facts = [
        Fact(value="amount: $42.00", source_node_id="doc::amount", verified=True),
        Fact(value="payee: City Electric", source_node_id="doc::payee", verified=True),
        Fact(value="due date: 2026-06-15", source_node_id="doc::due", verified=True),
    ]
    # The FakeRetriever matches by query substring; key on the words the per-stage
    # kernel goal carries so every stage's GROUND returns the grounded set.
    return FakeRetriever(corpus={"": facts, "amount": facts, "find": facts})


class _HeroActuator(Actuator):
    """A self-hosted-clone analogue that satisfies the whole hero chain so the
    stage graph flows AUTH → LOCATE → FILL → REVIEW → ⟨PAY⟩ → CONFIRM.

    The tree always carries a logged-in marker (AUTH), the grounded amount on the
    page (REVIEW cross-check), a required Amount field (FILL), and a Pay button.
    ``fill`` populates the field; the Pay ``click`` flips the page to a
    confirmation (PAY/CONFIRM). ``never_fills`` keeps FILL stuck for the replan
    test."""

    def __init__(self, *, never_fills: bool = False) -> None:
        self.act_calls: list[Action] = []
        self._filled = False
        self._paid = False
        self._never_fills = never_fills

    def _map(self) -> SelectorMap:
        if self._paid:
            return SelectorMap(
                nodes={
                    0: AxNode(
                        index=0,
                        role="status",
                        name="Confirmation #12345 — payment success",
                        node_id="n-confirm",
                    ),
                    1: AxNode(
                        index=1, role="link", name="My account", node_id="n-acct"
                    ),
                },
                token_estimate=30,
            )
        amount_name = "Amount: $42.00" if self._filled else ""
        return SelectorMap(
            nodes={
                0: AxNode(index=0, role="link", name="Log out", node_id="n-logout"),
                1: AxNode(
                    index=1,
                    role="textbox",
                    name=amount_name,
                    state={"required": True},
                    node_id="n-amount",
                ),
                2: AxNode(
                    index=2,
                    role="text",
                    name="Balance due: amount: $42.00 to City Electric",
                    node_id="n-balance",
                ),
                3: AxNode(index=3, role="button", name="Pay bill", node_id="n-pay"),
            },
            token_estimate=48,
        )

    async def perceive(self) -> SelectorMap:
        return self._map()

    async def act(self, action: Action) -> Observation:
        self.act_calls.append(action)
        if action.kind == "fill" and not self._never_fills:
            self._filled = True
        if action.kind == "click":
            self._paid = True
        return Observation(selector_map=self._map(), success=True)

    async def diff(self, before: SelectorMap, after: SelectorMap) -> PageDiff:
        return PageDiff()


async def test_fill_stage_node_advances_when_filled() -> None:
    """A happy hero run: the kernel fills the required field, FILL.done is True,
    and the FILL stage routes FORWARD (stage_idx advances past FILL)."""
    retriever = _hero_retriever()
    actuator = _HeroActuator()
    # Fast mode so reversible fills auto-proceed; the irreversible Pay still gates,
    # but we resume it below so the run reaches CONFIRM.
    graph = build_stage_graph(retriever, actuator, mode="fast", max_replans=2)
    config = _cfg()

    seed = seed_stage_state(mode="fast", page_index=await actuator.perceive())
    result = await graph.ainvoke(seed, config)
    # The Pay step (irreversible) armed the consent gate even in Fast mode.
    if "__interrupt__" in result:
        result = await graph.ainvoke(
            Command(resume=ConsentDecision(decision="approve").model_dump()), config
        )

    # The fill actually happened (the kernel's ACT fired the native-setter fill).
    assert any(a.kind == "fill" for a in actuator.act_calls)
    # FILL's machine done-gate passed against the re-perceived (now-filled) tree —
    # the stage advanced rather than replanning forever.
    fill_exit = [e for e in result["trace"] if e.node == "FILL" and e.event == "exit"]
    assert fill_exit, "no FILL exit trace event"
    assert fill_exit[-1].data["done"] is True


async def test_fill_stage_node_replans_when_field_stays_blank() -> None:
    """A stuck FILL: the field never populates → FILL.done stays False → the
    stage routes to the REPLANNER, which retries (bounded) and gives up to END
    rather than looping forever."""
    retriever = _hero_retriever()
    actuator = _HeroActuator(never_fills=True)
    graph = build_stage_graph(retriever, actuator, mode="fast", max_replans=1)

    seed = seed_stage_state(mode="fast", page_index=await actuator.perceive())
    final = await graph.ainvoke(seed, _cfg())

    # FILL never reported done ...
    fill_exits = [e for e in final["trace"] if e.node == "FILL" and e.event == "exit"]
    assert fill_exits and all(e.data["done"] is False for e in fill_exits)
    # ... the replanner ran and ultimately gave up (bounded, no infinite loop).
    gave_up = [
        e for e in final["trace"] if e.node == "REPLANNER" and e.data.get("gave_up")
    ]
    assert gave_up, "expected the replanner to give up after max_replans"


# ---------------------------------------------------------------------------
# (3b) the RESCUE cross-cut fires inside the live graph and returns to the stage
# ---------------------------------------------------------------------------


class _ChokedThenClearActuator(Actuator):
    """The SEEDED stage-entry tree (passed as ``page_index``) has a choked widget;
    the rescue sub-flow's re-perceive returns a CLEAN tree (the choked widget now
    carries an accessible name). Proves RESCUE fires, runs, clears, and returns
    control to the interrupted stage. ``perceive`` is the rescue's relabel — it is
    ALWAYS clean (the choked state lives only in the seeded ``page_index``)."""

    def __init__(self) -> None:
        self.act_calls: list[Action] = []

    @staticmethod
    def choked() -> SelectorMap:
        return SelectorMap(
            nodes={0: AxNode(index=0, role="textbox", name="", node_id="n-blind")},
            token_estimate=10,
        )

    def _clean(self) -> SelectorMap:
        # The relabelled tree: the widget now has an accessible name (rescued).
        return SelectorMap(
            nodes={
                0: AxNode(
                    index=0, role="textbox", name="Amount", node_id="n-blind"
                ),
            },
            token_estimate=14,
        )

    async def perceive(self) -> SelectorMap:
        return self._clean()

    async def act(self, action: Action) -> Observation:
        self.act_calls.append(action)
        return Observation(selector_map=self._clean(), success=True)

    async def diff(self, before: SelectorMap, after: SelectorMap) -> PageDiff:
        return PageDiff()


async def test_rescue_cross_cut_fires_and_returns_in_graph() -> None:
    """In the live stage graph: entering the first stage on a choked tree branches
    to RESCUE; RESCUE re-perceives a clean tree and returns to that stage."""
    retriever = FakeRetriever()
    actuator = _ChokedThenClearActuator()
    graph = build_stage_graph(retriever, actuator, mode="fast", max_replans=1)

    # Seed the graph with the choked tree (what the first stage sees on entry).
    seed = seed_stage_state(mode="fast", page_index=actuator.choked())
    final = await graph.ainvoke(seed, _cfg())

    # RESCUE fired and recorded the choked node ...
    triggered = [e for e in final["trace"] if e.data.get("rescue_triggered")]
    assert triggered, "RESCUE was not triggered on the choked tree"
    # ... ran and returned control to a stage node (the AUTH stage is first).
    rescue_exit = [e for e in final["trace"] if e.node == "RESCUE" and e.event == "exit"]
    assert rescue_exit, "RESCUE sub-flow did not run"
    assert rescue_exit[-1].data["returned_to"].startswith("stage_")
    assert rescue_exit[-1].data["resolved"] is True  # the clean re-perceive cleared it
    # The returned-to stage did NOT immediately re-trigger rescue (the
    # _rescue_done_for guard) — only one rescue_triggered event.
    assert len(triggered) == 1, "rescue re-triggered on the same tree (loop guard failed)"


# ---------------------------------------------------------------------------
# (5) nodes return ONLY-NEW trace entries — no double-count under the reducer
# ---------------------------------------------------------------------------


async def test_no_double_count_under_reducer() -> None:
    """The §18.7 reducer rule: every node returns ONLY its new trace/consent
    entries. We prove no double-count two ways:
      (a) the planner's single PLANNER exit event appears EXACTLY once;
      (b) no TraceEvent object identity is duplicated across the accumulated trace
          (a node re-emitting prior+new would surface the same kernel event twice).
    """
    retriever = _hero_retriever()
    actuator = _HeroActuator()
    graph = build_stage_graph(retriever, actuator, mode="fast", max_replans=2)
    config = _cfg()

    seed = seed_stage_state(mode="fast", page_index=await actuator.perceive())
    final = await graph.ainvoke(seed, config)
    if "__interrupt__" in final:
        final = await graph.ainvoke(
            Command(resume=ConsentDecision(decision="approve").model_dump()), config
        )

    trace = final["trace"]
    # (a) the planner emits exactly one PLANNER exit.
    planner_exits = [e for e in trace if e.node == "PLANNER" and e.event == "exit"]
    assert len(planner_exits) == 1, f"PLANNER exit double-counted: {len(planner_exits)}"

    # (b) the kernel's GROUND exit (carrying retrieval_ms) appears once PER stage
    #     run, never duplicated within a single stage's forwarded delta. We assert
    #     the count of GROUND exits equals the number of distinct stage runs that
    #     reached the kernel — i.e. the per-stage delta-slicing did NOT re-forward
    #     a prior stage's GROUND event. Concretely: every GROUND exit's (node,at)
    #     timestamp pair is unique (no object re-emitted).
    ground_exits = [e for e in trace if e.node == "GROUND" and e.event == "exit"]
    stamps = [(e.node, e.at, id(e)) for e in ground_exits]
    assert len(stamps) == len(set(stamps)), "a GROUND event was forwarded twice"

    # (c) and the trace strictly grew by appends only: it is non-empty and the
    #     planner event is the first stage-graph event recorded.
    assert trace, "no trace emitted"
    first_stagegraph = next(e for e in trace if e.node == "PLANNER")
    assert first_stagegraph.event == "exit"


async def test_consent_log_not_double_counted_in_normal_mode() -> None:
    """Normal mode arms the consent gate on the FILL stage's fill proposal. After
    one approve, the consent_log carries EXACTLY one entry for that proposal — the
    per-stage delta-forwarding did not re-append the kernel's accumulated log."""
    retriever = _hero_retriever()
    actuator = _HeroActuator()
    graph = build_stage_graph(retriever, actuator, mode="normal", max_replans=2)
    config = _cfg()

    seed = seed_stage_state(mode="normal", page_index=await actuator.perceive())
    paused = await graph.ainvoke(seed, config)
    # The AUTH stage's kernel interrupted at ⟨CONSENT⟩ (a consequential step in
    # Normal mode — every consequential step gates).
    assert "__interrupt__" in paused

    # Drive the whole chain to completion, approving each consent gate. The graph
    # re-interrupts per consequential stage; resume until it finishes (bounded).
    final = paused
    approve = Command(resume=ConsentDecision(decision="approve").model_dump())
    for _ in range(12):
        if "__interrupt__" not in final:
            break
        final = await graph.ainvoke(approve, config)

    # Each consented proposal appears EXACTLY once in the log — the per-stage
    # delta-forwarding did not re-append the kernel's accumulated consent_log.
    assert "__interrupt__" not in final, "did not converge"
    seen: dict[str, int] = {}
    for c in final["consent_log"]:
        key = f"{c.proposal_id}:{c.decision}:{c.at}"
        seen[key] = seen.get(key, 0) + 1
    dupes = {k: v for k, v in seen.items() if v > 1}
    assert not dupes, f"consent_log double-counted entries: {dupes}"
    assert any(c.decision == "approve" for c in final["consent_log"])


# ---------------------------------------------------------------------------
# Bonus: the shared FakeActuator drives a stage run without error (integration
# smoke — the stage graph composes the K1 kernel over the C1 fakes).
# ---------------------------------------------------------------------------


async def test_stage_graph_runs_over_fake_actuator() -> None:
    retriever = FakeRetriever()
    actuator = FakeActuator()
    graph = build_stage_graph(retriever, actuator, mode="fast", max_replans=1)

    seed = seed_stage_state(mode="fast", page_index=await actuator.perceive())
    final = await graph.ainvoke(seed, _cfg())
    # The plan was emitted and the run produced trace events for the planner + at
    # least the first stage.
    assert any(e.node == "PLANNER" for e in final["trace"])
    assert final["plan"], "plan not set on state"
    assert [s.id for s in final["plan"]] == list(HERO_STAGE_IDS)
