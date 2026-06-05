"""Wave-A contract tests for the de-hardcoding boundary (architecture Components +
killer-closers #1/#2/#3): the additive ``Fact.id`` / ``PairedFact`` /
``Subgoal`` / ``StepProposal`` shapes, the ``Reasoner`` ABC, the ``FakeReasoner``,
and the pure post-decode ``reasoner_guard``.

Pure: pydantic + the fakes + the kernel guard. Imports ZERO provider SDKs.
"""

from __future__ import annotations

import pytest

from clarion.contracts.ports import Reasoner
from clarion.contracts.state import (
    Fact,
    PageReadout,
    PairedFact,
    SelectorMap,
    StepProposal,
    Subgoal,
)
from clarion.contracts.state import AxNode
from clarion.fakes import FakeReasoner
from clarion.kernel.reasoner_guard import (
    GuardResult,
    resolve_value_ref,
    validate_step_proposal,
)


# ---------------------------------------------------------------------------
# Fact.id — stable, deterministic, additive
# ---------------------------------------------------------------------------


def test_fact_id_is_deterministic_and_content_addressed() -> None:
    # Equal content + source_node_id (+ polarity) => equal id.
    a = Fact(value="$84.32", source_node_id="ax-17", verified=True, retrieved_at=1.0)
    b = Fact(value="$84.32", source_node_id="ax-17", verified=False, retrieved_at=999.0)
    assert a.id == b.id  # verified / retrieved_at do NOT perturb the id

    # Different value OR different node OR different polarity => different id.
    assert a.id != Fact(value="$84.33", source_node_id="ax-17").id
    assert a.id != Fact(value="$84.32", source_node_id="ax-18").id
    assert a.id != Fact(value="$84.32", source_node_id="ax-17", polarity="absent").id

    # It is a stable string handle (the value_ref enum points at it).
    assert isinstance(a.id, str) and a.id.startswith("fact-")


def test_fact_id_survives_model_dump() -> None:
    # computed_field serializes so a checkpointed Fact still carries its id.
    f = Fact(value="June 15, 2026", source_node_id="ax-3")
    dumped = f.model_dump()
    assert dumped["id"] == f.id


# ---------------------------------------------------------------------------
# PairedFact — the geometric label↔value pairing (killer-closer #1)
# ---------------------------------------------------------------------------


def test_paired_fact_backs_both_halves_only_for_one_pairing() -> None:
    label = Fact(value="Amount due", source_node_id="ax-label")
    value = Fact(value="$84.32", source_node_id="ax-value")
    pair = PairedFact(label=label, value=value, method="shared-row")

    # "X is Y" is speakable: the single pairing grounds BOTH halves, byte-identical.
    assert pair.backs("Amount due", "$84.32") is True

    # A mis-pairing (the past-due row's number) is NOT backed by THIS pairing.
    assert pair.backs("Amount due", "$142.10") is False
    assert pair.backs("Past due", "$84.32") is False

    # The pairing method is structural, never reading-order.
    assert pair.method in ("aria-labelledby", "for", "shared-row", "dom-ancestry")
    assert pair.id.startswith("pair-")


def test_paired_fact_refuses_when_a_half_is_ungrounded() -> None:
    # An ungrounded half (source_node_id=None) can never back a speakable claim.
    label = Fact(value="Amount due", source_node_id=None)
    value = Fact(value="$84.32", source_node_id="ax-value")
    pair = PairedFact(label=label, value=value, method="dom-ancestry")
    assert pair.backs("Amount due", "$84.32") is False


# ---------------------------------------------------------------------------
# Reasoner ABC + FakeReasoner
# ---------------------------------------------------------------------------


def test_reasoner_is_abstract_and_fake_satisfies_it() -> None:
    with pytest.raises(TypeError):
        Reasoner()  # type: ignore[abstract]
    assert isinstance(FakeReasoner(), Reasoner)


@pytest.mark.asyncio
async def test_fake_reasoner_default_points_at_real_index_and_fact() -> None:
    node = AxNode(index=2, role="link", name="Check status", node_id="ax-2")
    live = SelectorMap(nodes={2: node})
    facts = [Fact(value="$84.32", source_node_id="ax-2")]

    reasoner = FakeReasoner()
    plan = await reasoner.plan_goal("find the amount", PageReadout(), facts)
    assert plan and isinstance(plan[0], Subgoal)

    step = await reasoner.decide_step("find the amount", live, facts, history=[])
    # Default fake points at the live index and references the real Fact id.
    assert step.target_index == 2
    assert step.value_ref == facts[0].id
    assert reasoner.decide_calls == ["find the amount"]


@pytest.mark.asyncio
async def test_fake_reasoner_is_scriptable() -> None:
    seeded = [
        StepProposal(action_kind="fill", target_index=0, value_ref="fact-abc"),
        StepProposal(action_kind="click", target_index=1),
    ]
    reasoner = FakeReasoner(
        subgoals=[Subgoal(description="seeded", done_check="navigated")],
        steps=seeded,
    )
    plan = await reasoner.plan_goal("g", PageReadout(), [])
    assert plan[0].description == "seeded"

    live = SelectorMap()
    s1 = await reasoner.decide_step("g", live, [], [])
    s2 = await reasoner.decide_step("g", live, [], [])
    s3 = await reasoner.decide_step("g", live, [], [])  # exhausted -> repeats last
    assert (s1.action_kind, s2.action_kind, s3.action_kind) == ("fill", "click", "click")


# ---------------------------------------------------------------------------
# reasoner_guard — the code-side post-decode fence
# ---------------------------------------------------------------------------


def _live_map() -> SelectorMap:
    return SelectorMap(
        nodes={
            0: AxNode(index=0, role="textbox", name="Amount", node_id="ax-0"),
            1: AxNode(index=1, role="button", name="Submit", node_id="ax-1"),
        }
    )


def test_guard_accepts_real_index_and_resolvable_value_ref() -> None:
    facts = [Fact(value="$84.32", source_node_id="ax-0")]
    step = StepProposal(action_kind="fill", target_index=0, value_ref=facts[0].id)
    result = validate_step_proposal(step, _live_map(), facts)
    assert isinstance(result, GuardResult) and result.ok is True


def test_guard_accepts_null_value_ref_for_a_click() -> None:
    step = StepProposal(action_kind="click", target_index=1, value_ref=None)
    result = validate_step_proposal(step, _live_map(), facts=[])
    assert result.ok is True


def test_guard_rejects_off_page_target_index() -> None:
    step = StepProposal(action_kind="click", target_index=99)
    result = validate_step_proposal(step, _live_map(), facts=[])
    assert result.ok is False and "99" in result.reason


def test_guard_rejects_dangling_value_ref() -> None:
    facts = [Fact(value="$84.32", source_node_id="ax-0")]
    step = StepProposal(action_kind="fill", target_index=0, value_ref="fact-DANGLING")
    result = validate_step_proposal(step, _live_map(), facts)
    assert result.ok is False and "value_ref" in result.reason


def test_resolve_value_ref_returns_the_real_fact() -> None:
    facts = [Fact(value="$84.32", source_node_id="ax-0")]
    assert resolve_value_ref(facts[0].id, facts) is facts[0]
    assert resolve_value_ref(None, facts) is None
    assert resolve_value_ref("fact-DANGLING", facts) is None
