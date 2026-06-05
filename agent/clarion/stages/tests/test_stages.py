"""Wave-C Step-7 — the GENERIC INVARIANT SPEC (the red-before-green guard).

This file REPLACES the quarantined legacy pay-topology acceptance suite. The 12
old tests pinned the DELETED hardcoded ``AUTH→LOCATE→FILL→REVIEW→⟨PAY⟩→CONFIRM``
topology, the §3.2 done-predicate/negative-check table, and the ``_hero_plan``
shape — every one of those symbols is gone (the strangler migration replaced the
baked stages with a Reasoner-derived ``list[Subgoal]`` + the generic executor).
ZERO topology assertions and no module skip remain.

In their place: the goal-AGNOSTIC invariant battery from migration **Step 7**,
asserted through the PUBLIC interface (``build_kernel`` / ``build_stage_graph``
driven by a scripted ``FakeReasoner`` — BEHAVIOR, never internals). It is the guard
that a silent invariant weakening (a fabricated value spoken, a model-reversible
submit auto-acted in Fast, a confident negative on an unread region, a no-op
advancing, a baked stage name reappearing) is caught RED before green.

The two invariants under guard (architecture "The two invariants, enforced in code"):
  - **Epistemic** — no fact without a source; verbatim + paired-grounded speech.
  - **Agentic** — no consequential act without a yes (dual-signal fail-closed gate).

Network-free: FakeReasoner only — no real LLM / site / keys. Deterministic.

------------------------------------------------------------------------------
Step-7 coverage matrix (invariant → the test that pins it; ✦ = added here, the
gaps the audit showed were NOT yet pinned through the public interface; the rest
are cross-referenced to the owning suite so this spec consolidates without
duplicating an already-green assertion):

  1. ungrounded value (no source) → refused, never spoken
        ✦ test_ungrounded_value_is_never_filled_or_spoken (build_kernel)
        ↔ kernel/tests/test_kernel.py::test_verify_node_refuses_ungrounded_fact_in_graph
  2. mispaired value (no single backing PairedFact) → refused
        ✦ test_fabricated_value_ref_is_dropped_not_spoken (build_kernel)
        ↔ kernel/tests/test_kernel.py::test_pairing_fence_needs_a_single_backing_pair
        ↔ tests/test_paired_facts.py::test_table_refuses_the_cross_row_mispairing
        ↔ tests/test_paired_facts.py::test_shared_row_refuses_when_two_values_tie
  3. uncovered / truncated-harvest negative → HEDGE, not a confident "no X"
        ↔ kernel/tests/test_gate_wiring.py::test_uncovered_negative_is_hedged_not_spoken
        ↔ kernel/tests/test_negative_verifier.py::test_uncovered_negative_hedges_image_rendered_charge
  4. a model-``reversible`` submit the structural net escalates → can't reach ACT
     in Fast (routes through CONSENT)
        ✦ test_model_reversible_but_structurally_escalated_submit_gates_in_fast (build_stage_graph)
        ↔ kernel/tests/test_irreversibility_gate.py::test_model_reversible_cannot_downgrade_structural_escalation
        ↔ kernel/tests/test_kernel.py::test_fast_mode_still_interrupts_irreversible
  5. a no-op step (page unchanged) → does NOT advance
        ↔ stages/tests/test_executor.py::test_noop_step_is_failed_not_advanced_across_every_check
        ↔ stages/tests/test_executor.py::test_executor_replans_then_gives_up_on_stuck_subgoal
  6. a goal-derived plan is goal-agnostic (no baked stage names / "pay electric bill")
        ✦ test_plan_carries_no_baked_pay_topology (build_stage_graph, end-to-end)
        ↔ stages/tests/test_executor.py::test_planner_emits_goal_derived_subgoals
        ↔ stages/tests/test_executor.py::test_planner_falls_open_to_generic_subgoal_on_empty
------------------------------------------------------------------------------
"""

from __future__ import annotations

import uuid

import pytest
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
    StepProposal,
    Subgoal,
)
from clarion.fakes import FakeActuator, FakeReasoner, FakeRetriever
from clarion.kernel.graph import build_kernel, seed_state
from clarion.kernel.policy import is_speakable_value, speakable
from clarion.stages.graph import build_stage_graph, seed_stage_state


def _cfg() -> dict:
    return {"configurable": {"thread_id": str(uuid.uuid4())}}


# ---------------------------------------------------------------------------
# Scriptable fakes — deterministic, network-free.
# ---------------------------------------------------------------------------


class _ScriptedRetriever(Retriever):
    """Returns exactly the facts handed in (ungrounded ones included), so the
    epistemic refusal paths are drivable verbatim."""

    def __init__(self, facts: list[Fact]) -> None:
        self._facts = facts

    async def query(self, q: str, *, k: int = 5) -> list[Fact]:  # noqa: ARG002
        return list(self._facts)[:k]


class _FillActuator(Actuator):
    """A one-textbox + one-button page. ``act`` records the action so we can prove
    BY CALL whether a value was ever filled (the epistemic side-effect fence)."""

    def __init__(self) -> None:
        self.act_calls: list[Action] = []
        self._filled_value: str | None = None

    def _map(self) -> SelectorMap:
        name = f"Amount: {self._filled_value}" if self._filled_value else "Amount"
        return SelectorMap(
            nodes={
                0: AxNode(index=0, role="textbox", name=name, node_id="n-amount"),
                1: AxNode(index=1, role="button", name="Continue", node_id="n-c"),
            },
            token_estimate=20,
        )

    async def perceive(self) -> SelectorMap:
        return self._map()

    async def act(self, action: Action) -> Observation:
        self.act_calls.append(action)
        if action.kind == "fill" and action.value is not None:
            self._filled_value = action.value
        return Observation(selector_map=self._map(), success=True)

    async def diff(self, before: SelectorMap, after: SelectorMap) -> PageDiff:
        return PageDiff()


def _fill_step(*, value_ref: str | None, say: str) -> FakeReasoner:
    """A reasoner scripted to fill index 0 with ``value_ref`` (reversible)."""
    return FakeReasoner(
        steps=[
            StepProposal(
                scratch_reasoning="fill the amount",
                action_kind="fill",
                target_index=0,
                value_ref=value_ref,
                irreversibility="reversible",
                success_check="field_nonempty",
                say=say,
            )
        ]
    )


# ===========================================================================
# INVARIANT 1 (epistemic) — an UNGROUNDED value is refused: never filled, never
# spoken. New behavioral pin through build_kernel: the kernel must not turn a
# source-less fact into a side-effect or an utterance.
# ===========================================================================


async def test_ungrounded_value_is_never_filled_or_spoken() -> None:
    """An ungrounded fact (``source_node_id is None``) reaches GROUND, but VERIFY
    refuses it and the membership fence (#2) drops it: it is neither filled into the
    field nor read back as a spoken value. The kernel falls open to a safe
    read-back, but the ungrounded string never appears in it.

    (Cross-ref: VERIFY's refusal flag itself is pinned in
    kernel/tests/test_kernel.py::test_verify_node_refuses_ungrounded_fact_in_graph;
    here we pin the DOWNSTREAM behavior — it cannot be spoken or acted.)"""
    ungrounded = Fact(value="made-up $999.00", source_node_id=None)
    retriever = _ScriptedRetriever([ungrounded])
    actuator = _FillActuator()
    # The reasoner references the ungrounded fact's id — the guard accepts the id
    # (it IS a live Fact.id), but is_speakable_value rejects the unverified value.
    graph = build_kernel(
        _fill_step(value_ref=ungrounded.id, say="made-up $999.00"),
        retriever,
        actuator,
        mode="fast",
    )
    seed = seed_state(goal="enter the amount", mode="fast")
    seed["page_index"] = await actuator.perceive()
    final = await graph.ainvoke(seed, _cfg())

    # Epistemic clause: the source-less value was never filled into the field …
    assert all(a.value != "made-up $999.00" for a in actuator.act_calls)
    assert actuator._filled_value is None
    # … and never surfaced in the spoken proposal.
    assert "made-up" not in final["pending_proposal"].utterance.lower()
    assert "$999" not in final["pending_proposal"].utterance
    # The grounded set carries the fact but it is refused (verified False, unspeakable).
    assert speakable(final["grounded_facts"]) == []


# ===========================================================================
# INVARIANT 2 (epistemic) — a MISPAIRED / fabricated value with no grounded
# backing is refused. The kernel only ever fills/speaks a value that resolves to a
# real Fact.id AND is a live grounded member; a value_ref the model invented (or a
# paraphrase it tried to substitute) is dropped — never spoken.
#
# The geometric mis-pairing itself (a clean citation on the wrong number) is the
# extract-time fence, pinned at the policy + extractor level (cross-refs below).
# Through build_kernel the testable behavior is the value-fabrication refusal: the
# kernel never emits an "X is Y" the grounded set doesn't back.
# ===========================================================================


async def test_fabricated_value_ref_is_dropped_not_spoken() -> None:
    """The reasoner points ``value_ref`` at an id that resolves to NO live Fact (a
    value the model fabricated rather than read off a grounded span). The
    reasoner_guard rejects the off-grounding proposal → the kernel discards it for a
    safe read-back; the fabricated value is never filled and never spoken.

    (Cross-ref for the geometric mis-pairing refusal itself:
    kernel/tests/test_kernel.py::test_pairing_fence_needs_a_single_backing_pair and
    tests/test_paired_facts.py::{test_table_refuses_the_cross_row_mispairing,
    test_shared_row_refuses_when_two_values_tie}.)"""
    grounded = Fact(value="$84.32", source_node_id="n-amount", verified=True)
    retriever = _ScriptedRetriever([grounded])
    actuator = _FillActuator()
    graph = build_kernel(
        _fill_step(value_ref="fact-deadbeefdeadbeef", say="$142.10"),  # dangling id
        retriever,
        actuator,
        mode="fast",
    )
    seed = seed_state(goal="enter the amount", mode="fast")
    seed["page_index"] = await actuator.perceive()
    final = await graph.ainvoke(seed, _cfg())

    # The fabricated/mispaired value never reached the page as a side-effect: the
    # discarded proposal degrades to a harmless read-back, so NO fill (and no
    # action carrying the fabricated value) is ever executed.
    assert all(a.kind != "fill" for a in actuator.act_calls)
    assert all(a.value != "$142.10" for a in actuator.act_calls)
    assert actuator._filled_value is None
    # … and is not in the utterance (the dangling-ref discard left a grounded
    # read-back of the REAL fact only).
    assert "$142.10" not in final["pending_proposal"].utterance
    # The guard recorded the rejection (audit trail of the refusal).
    rejected = [e for e in final["trace"] if e.node == "PROPOSE" and "rejected" in e.data]
    assert rejected, "expected a PROPOSE rejection of the dangling value_ref"
    # Sanity: the real grounded value WOULD be speakable (the fence is selective,
    # not a blanket mute).
    assert is_speakable_value("$84.32", final["grounded_facts"]) is True


# ===========================================================================
# INVARIANT 3 (epistemic) — an uncovered / truncated-harvest negative HEDGES.
# Fully owned by the AG-GATE suite through the public build_kernel API; pinned
# end-to-end there. Cross-referenced (no re-assertion) to keep this spec a single
# coherent, non-duplicating battery:
#   ↔ kernel/tests/test_gate_wiring.py::test_uncovered_negative_is_hedged_not_spoken
#   ↔ kernel/tests/test_gate_wiring.py::test_covered_negative_is_spoken
#   ↔ kernel/tests/test_negative_verifier.py::test_uncovered_negative_hedges_image_rendered_charge
# ===========================================================================


# ===========================================================================
# INVARIANT 4 (agentic) — a control the MODEL judges ``reversible`` that the
# independent structural net escalates (no grounded undo) CANNOT reach ACT in Fast
# mode: it routes through CONSENT. New end-to-end pin through the EXECUTOR (the
# public stage graph) — the dual-signal gate fires under the real run, not just in
# the classifier unit. (Classifier unit: test_irreversibility_gate.py.)
# ===========================================================================


class _SubmitActuator(Actuator):
    """A page with ONE consequential, benignly-named control and NO undo/cancel
    affordance anywhere — the structural net's UNKNOWN-on-no-undo trigger."""

    def __init__(self) -> None:
        self.act_calls: list[Action] = []

    def _map(self) -> SelectorMap:
        return SelectorMap(
            nodes={0: AxNode(index=0, role="button", name="Continue", node_id="n-c")},
            token_estimate=10,
        )

    async def perceive(self) -> SelectorMap:
        return self._map()

    async def act(self, action: Action) -> Observation:
        self.act_calls.append(action)
        return Observation(selector_map=self._map(), success=True)

    async def diff(self, before: SelectorMap, after: SelectorMap) -> PageDiff:
        return PageDiff()


def _reversible_click_reasoner() -> FakeReasoner:
    """The model is CONFIDENT this consequential click is reversible — the worst
    agentic failure mode (a benignly-named control that mutates). One subgoal so the
    executor runs exactly this step."""
    return FakeReasoner(
        subgoals=[Subgoal(description="proceed", done_check="node_added")],
        steps=[
            StepProposal(
                scratch_reasoning="this looks like a harmless next button",
                action_kind="click",
                target_index=0,
                value_ref=None,
                irreversibility="reversible",  # the model is wrong / over-confident
                success_check="node_added",
                say="",
            )
        ],
    )


async def test_model_reversible_but_structurally_escalated_submit_gates_in_fast() -> None:
    """Through the public stage graph in FAST mode: the model says ``reversible``,
    but the structural pre-screen escalates a consequential control with no grounded
    undo to ``unknown`` → the step CANNOT auto-act; it routes through CONSENT and
    the executor re-surfaces the interrupt. Nothing is clicked before the yes.

    The agentic invariant survives the de-hardcoding: the model can never downgrade
    the structural net (cross-ref:
    kernel/tests/test_irreversibility_gate.py::test_model_reversible_cannot_downgrade_structural_escalation)."""
    reasoner = _reversible_click_reasoner()
    actuator = _SubmitActuator()
    graph = build_stage_graph(
        reasoner, FakeRetriever(), actuator, mode="fast", max_replans=1
    )
    config = _cfg()
    seed = seed_stage_state(
        goal="proceed", mode="fast", page_index=await actuator.perceive()
    )

    paused = await graph.ainvoke(seed, config)
    # Fast mode would auto-act a truly reversible step — but the structural
    # escalation forced a consent beat instead.
    assert "__interrupt__" in paused
    assert actuator.act_calls == []  # NO side-effect before the yes

    # And the surfaced consent flags it as gated (irreversible/unknown).
    (interrupt_obj,) = paused["__interrupt__"]
    assert interrupt_obj.value["irreversible"] is True

    # On approve it proceeds (the gate is a checkpoint, not a dead-end).
    final = await graph.ainvoke(
        Command(resume=ConsentDecision(decision="approve").model_dump()), config
    )
    assert any(a.kind == "click" for a in actuator.act_calls)
    assert any(c.decision == "approve" for c in final["consent_log"])


# ===========================================================================
# INVARIANT 5 (agentic / done-check) — a NO-OP step does not advance. Fully owned
# by AG-DONE through the generic check evaluator AND the live executor giving-up
# path. Cross-referenced (no re-assertion):
#   ↔ stages/tests/test_executor.py::test_noop_step_is_failed_not_advanced_across_every_check
#   ↔ stages/tests/test_executor.py::test_executor_replans_then_gives_up_on_stuck_subgoal
# ===========================================================================


# ===========================================================================
# INVARIANT 6 (de-hardcoding) — the plan is GOAL-DERIVED and goal-AGNOSTIC: no
# baked AUTH→…→CONFIRM stage names, no "pay electric bill" topology. AG-DONE pins
# the planner unit (test_executor.py); here we add the END-TO-END pin: a full run
# over the public stage graph never materializes the deleted topology on state.
# ===========================================================================


async def test_plan_carries_no_baked_pay_topology() -> None:
    """A full executor run with a Reasoner-derived plan: the spoken plan + the
    subgoals on state are exactly what the reasoner returned — none of the DELETED
    hardcoded stage names (AUTH/LOCATE/FILL/REVIEW/PAY/CONFIRM) or the baked
    "pay … electric bill" goal appears anywhere. This is the guard that a baked
    topology cannot silently creep back in via the planner.

    (Planner-unit cross-ref:
    stages/tests/test_executor.py::test_planner_emits_goal_derived_subgoals.)"""
    reasoner = FakeReasoner(
        subgoals=[
            Subgoal(description="find the application status", done_check="confirmation_fact"),
            Subgoal(description="read it back to me", done_check="confirmation_fact"),
        ]
    )
    actuator = FakeActuator()
    graph = build_stage_graph(
        reasoner, FakeRetriever(), actuator, mode="fast", max_replans=1
    )
    seed = seed_stage_state(
        goal="check my benefits status", mode="fast", page_index=await actuator.perceive()
    )
    final = await graph.ainvoke(seed, _cfg())

    # The plan is the reasoner's, derived from the goal.
    assert [s.description for s in final["subgoals"]] == [
        "find the application status",
        "read it back to me",
    ]
    # No DELETED hardcoded stage id survives anywhere on the plan or its done-checks.
    _BAKED = {"auth", "locate", "fill", "review", "pay", "confirm"}
    for s in final["subgoals"]:
        words = set(s.description.lower().split()) | {s.done_check.lower()}
        assert not (words & _BAKED), f"baked topology leaked into subgoal: {s}"
    # The legacy goal string is gone.
    plan_blob = " ".join(s.description.lower() for s in final["subgoals"])
    assert "pay" not in plan_blob and "electric bill" not in plan_blob
    # The planner ran and spoke the goal-derived plan (legibility beat).
    planner_exit = next(e for e in final["trace"] if e.node == "PLANNER")
    assert "here's my plan" in str(planner_exit.data.get("utterance", "")).lower()


# ===========================================================================
# Bonus: the spine still composes over the shared fakes (the K1 kernel under the
# generic executor over the C1 fakes) — a smoke that the public surface is wired,
# with NO topology assertion (the deleted `HERO_STAGE_IDS` check is gone).
# ===========================================================================


async def test_stage_graph_composes_over_fakes_no_topology() -> None:
    reasoner = FakeReasoner()  # default: one generic subgoal naming the goal
    retriever = FakeRetriever()
    actuator = FakeActuator()
    graph = build_stage_graph(reasoner, retriever, actuator, mode="fast", max_replans=1)

    seed = seed_stage_state(
        goal="do the private task", mode="fast", page_index=await actuator.perceive()
    )
    final = await graph.ainvoke(seed, _cfg())

    # The planner ran and a goal-derived plan landed on state — not a baked list.
    assert any(e.node == "PLANNER" for e in final["trace"])
    assert final["subgoals"], "no subgoals on state"
    # The single generic fallback subgoal names the real goal, not a topology.
    assert "do the private task" in final["subgoals"][0].description.lower()
