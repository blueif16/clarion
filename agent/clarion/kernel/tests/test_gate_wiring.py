"""AG-GATE — the honest-decline + Fast-cap wired through ``build_kernel``.

End-to-end (FakeReasoner-driven, network-free) proof that:

  (A) a reasoner ``say`` that asserts a NEGATIVE ("no late fee") on UNCOVERED data
      (an image-rendered charge → no grounded fact) is HEDGED by the kernel — the
      surfaced utterance is NOT a confident "no late fee" (the killer acceptance);
  (B) the SAME negative WITH a grounded ``absent`` coverage fact is spoken verbatim;
  (C) the Fast-cap routes even a REVERSIBLE first act through CONSENT when the
      silent-act budget is zero (``fast_act_cap=0``), and ACT records the counter.

Pure: FakeReasoner/scripted fakes; ZERO provider SDK.
"""

from __future__ import annotations

import uuid

import pytest

from clarion.contracts.ports import Actuator, Retriever
from clarion.contracts.state import (
    Action,
    AxNode,
    Fact,
    Observation,
    PageDiff,
    SelectorMap,
    StepProposal,
)
from clarion.fakes import FakeActuator, FakeReasoner
from clarion.kernel.graph import build_kernel, seed_state


def _cfg() -> dict:
    return {"configurable": {"thread_id": str(uuid.uuid4())}}


class _ScriptedRetriever(Retriever):
    def __init__(self, facts: list[Fact]) -> None:
        self._facts = facts

    async def query(self, q: str, *, k: int = 5) -> list[Fact]:  # noqa: ARG002
        return list(self._facts)[:k]


class _ReadActuator(Actuator):
    """A one-node page so a read step has a live target index."""

    def __init__(self) -> None:
        self.act_calls: list[Action] = []

    def _map(self) -> SelectorMap:
        return SelectorMap(
            nodes={0: AxNode(index=0, role="text", name="Account", node_id="n-acct")},
            token_estimate=10,
        )

    async def perceive(self) -> SelectorMap:
        return self._map()

    async def act(self, action: Action) -> Observation:
        self.act_calls.append(action)
        return Observation(selector_map=self._map(), success=True)

    async def diff(self, before: SelectorMap, after: SelectorMap) -> PageDiff:
        return PageDiff()


def _negative_say_reasoner(say: str) -> FakeReasoner:
    """A reasoner that reads back a NEGATIVE claim (``say``)."""
    return FakeReasoner(
        steps=[
            StepProposal(
                scratch_reasoning="report the negative",
                action_kind="read",
                target_index=0,
                value_ref=None,
                irreversibility="reversible",
                success_check="status-fact-appeared",
                say=say,
            )
        ]
    )


# ---------------------------------------------------------------------------
# (A) the killer acceptance: an uncovered negative HEDGES (no confident "no …")
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_uncovered_negative_is_hedged_not_spoken() -> None:
    # The page only grounds the amount due; the late fee is in an IMAGE → no fact.
    facts = [Fact(value="Amount due: $84.32", source_node_id="n-amt", verified=True)]
    retriever = _ScriptedRetriever(facts)
    actuator = _ReadActuator()
    graph = build_kernel(
        _negative_say_reasoner("no late fee"), retriever, actuator, mode="fast"
    )

    seed = seed_state(goal="is there a late fee", mode="fast")
    seed["page_index"] = await actuator.perceive()
    final = await graph.ainvoke(seed, _cfg())

    utterance = final["pending_proposal"].utterance.lower()
    # The agent did NOT speak a confident negative…
    assert "no late fee" not in utterance
    assert "no late" not in utterance
    # …it hedged.
    assert "guess" in utterance or "couldn't confirm" in utterance
    # A hedge trace marker is present.
    hedged = [e for e in final["trace"] if e.node == "PROPOSE" and "hedged" in e.data]
    assert hedged, "expected a PROPOSE hedge trace marker"


# ---------------------------------------------------------------------------
# (B) a covered negative is spoken verbatim (grounded absent fact)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_covered_negative_is_spoken() -> None:
    facts = [
        Fact(value="Amount due: $84.32", source_node_id="n-amt", verified=True),
        # We affirmatively READ "no late fee" off the perceived region.
        Fact(
            value="no late fee",
            source_node_id="n-fee",
            polarity="absent",
            verified=True,
        ),
    ]
    retriever = _ScriptedRetriever(facts)
    actuator = _ReadActuator()
    graph = build_kernel(
        _negative_say_reasoner("no late fee"), retriever, actuator, mode="fast"
    )

    seed = seed_state(goal="is there a late fee", mode="fast")
    seed["page_index"] = await actuator.perceive()
    final = await graph.ainvoke(seed, _cfg())

    utterance = final["pending_proposal"].utterance.lower()
    assert "no late fee" in utterance  # the grounded, covered negative is spoken
    hedged = [e for e in final["trace"] if e.node == "PROPOSE" and "hedged" in e.data]
    assert not hedged, "a covered negative must not be hedged"


# ---------------------------------------------------------------------------
# (C) the Fast-cap forces a consent beat after the silent-act budget
# ---------------------------------------------------------------------------


def _fill_reasoner(facts: list[Fact]) -> FakeReasoner:
    fid = facts[0].id if facts else None
    return FakeReasoner(
        steps=[
            StepProposal(
                scratch_reasoning="fill the amount",
                action_kind="fill",
                target_index=0,
                value_ref=fid,
                irreversibility="reversible",
                success_check="field_nonempty",
                say=facts[0].value if facts else "",
            )
        ]
    )


@pytest.mark.asyncio
async def test_fast_cap_zero_gates_even_a_reversible_first_act() -> None:
    """With ``fast_act_cap=0`` the silent reversible-act budget is zero, so even a
    reversible fill must route through CONSENT (a forced spoken progress beat) —
    the Step-5 cap mechanism, proven through the public build_kernel API."""
    facts = [Fact(value="$42.00", source_node_id="n-amount", verified=True)]
    retriever = _ScriptedRetriever(facts)
    actuator = FakeActuator()
    graph = build_kernel(
        _fill_reasoner(facts), retriever, actuator, mode="fast", fast_act_cap=0
    )

    seed = seed_state(goal="enter the amount", mode="fast")
    seed["page_index"] = await actuator.perceive()
    config = _cfg()

    result = await graph.ainvoke(seed, config)
    # The cap forced a consent prompt even though the act is reversible.
    assert "__interrupt__" in result
    assert graph.get_state(config).next == ("consent",)
    assert actuator.acted == []  # nothing acted before the forced beat


@pytest.mark.asyncio
async def test_fast_cap_one_allows_a_single_reversible_act() -> None:
    """The DEFAULT cap (1) lets the first reversible act auto-proceed (no
    regression on the existing Fast-mode behaviour) and ACT records fast_acts=1."""
    facts = [Fact(value="$42.00", source_node_id="n-amount", verified=True)]
    retriever = _ScriptedRetriever(facts)
    actuator = FakeActuator()
    graph = build_kernel(_fill_reasoner(facts), retriever, actuator, mode="fast")

    seed = seed_state(goal="enter the amount", mode="fast")
    seed["page_index"] = await actuator.perceive()
    config = _cfg()

    final = await graph.ainvoke(seed, config)
    assert "__interrupt__" not in final  # one reversible act is within budget
    assert len(actuator.acted) == 1
    # The counter advanced to the cap after the silent auto-act.
    assert final.get("fast_acts") == 1
