"""The grounded reversibility VERDICT spoken in the consent readback (Slice 1).

The IrreversibilityGate appends a clause to the consent ``utterance`` keyed on its
OWN classification (the kernel's computation), so the user HEARS whether a step can
be undone before they say yes — never the voice LLM's guess. End-to-end through the
public ``build_kernel`` API (FakeReasoner-driven, network-free):

  - a REVERSIBLE step's readback says it can be undone;
  - an IRREVERSIBLE step's readback says it can't;
  - a READ-back gets NO reversibility clause (no side-effect; never gated).

Pure: scripted fakes; ZERO provider SDK.
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
from clarion.kernel.graph import _REVERSIBILITY_NOTE, build_kernel, seed_state


def _cfg() -> dict:
    return {"configurable": {"thread_id": str(uuid.uuid4())}}


class _ScriptedRetriever(Retriever):
    def __init__(self, facts: list[Fact]) -> None:
        self._facts = facts

    async def query(self, q: str, *, k: int = 5) -> list[Fact]:  # noqa: ARG002
        return list(self._facts)[:k]


class _OneNodeActuator(Actuator):
    """A one-node page so a click/read step has a live target index."""

    def __init__(self, role: str, name: str) -> None:
        self._role = role
        self._name = name

    def _map(self) -> SelectorMap:
        return SelectorMap(
            nodes={0: AxNode(index=0, role=self._role, name=self._name, node_id="n-0")},
            token_estimate=10,
        )

    async def perceive(self) -> SelectorMap:
        return self._map()

    async def act(self, action: Action) -> Observation:  # noqa: ARG002
        return Observation(selector_map=self._map(), success=True)

    async def diff(self, before: SelectorMap, after: SelectorMap) -> PageDiff:  # noqa: ARG002
        return PageDiff()


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


def _click_reasoner() -> FakeReasoner:
    return FakeReasoner(
        steps=[
            StepProposal(
                scratch_reasoning="press the submit control",
                action_kind="click",
                target_index=0,
                value_ref=None,
                irreversibility="irreversible",
                success_check="navigated",
                say="",
            )
        ]
    )


def _read_reasoner(facts: list[Fact]) -> FakeReasoner:
    fid = facts[0].id if facts else None
    return FakeReasoner(
        steps=[
            StepProposal(
                scratch_reasoning="read it back",
                action_kind="read",
                target_index=0,
                value_ref=fid,
                irreversibility="reversible",
                success_check="status-fact-appeared",
                say=facts[0].value if facts else "",
            )
        ]
    )


@pytest.mark.asyncio
async def test_reversible_readback_says_it_can_be_undone() -> None:
    """A reversible fill, forced to CONSENT by a zero Fast-cap: the surfaced readback
    carries the GROUNDED 'I can undo this' clause keyed on the gate's verdict."""
    facts = [Fact(value="$42.00", source_node_id="n-amount", verified=True)]
    actuator = FakeActuator()
    graph = build_kernel(
        _fill_reasoner(facts),
        _ScriptedRetriever(facts),
        actuator,
        mode="fast",
        fast_act_cap=0,
    )
    seed = seed_state(goal="enter the amount", mode="fast")
    seed["page_index"] = await actuator.perceive()

    result = await graph.ainvoke(seed, _cfg())
    assert "__interrupt__" in result
    utterance = result["__interrupt__"][0].value["utterance"]
    assert _REVERSIBILITY_NOTE["reversible"] in utterance


@pytest.mark.asyncio
async def test_irreversible_readback_says_it_cannot_be_undone() -> None:
    """An irreversible click hard-stops at CONSENT and the readback carries the
    grounded 'this can't be undone' clause."""
    actuator = _OneNodeActuator(role="button", name="Submit payment")
    graph = build_kernel(_click_reasoner(), _ScriptedRetriever([]), actuator, mode="fast")
    seed = seed_state(goal="submit the payment", mode="fast")
    seed["page_index"] = await actuator.perceive()

    result = await graph.ainvoke(seed, _cfg())
    assert "__interrupt__" in result
    utterance = result["__interrupt__"][0].value["utterance"]
    assert _REVERSIBILITY_NOTE["irreversible"] in utterance


@pytest.mark.asyncio
async def test_read_back_gets_no_reversibility_clause() -> None:
    """A read performs no mutation and never surfaces at a gate, so its readback must
    NOT acquire a spurious 'I can undo this' clause."""
    facts = [Fact(value="Amount due: $84.32", source_node_id="n-amt", verified=True)]
    actuator = _OneNodeActuator(role="text", name="Account")
    graph = build_kernel(_read_reasoner(facts), _ScriptedRetriever(facts), actuator, mode="fast")
    seed = seed_state(goal="what is the amount due", mode="fast")
    seed["page_index"] = await actuator.perceive()

    final = await graph.ainvoke(seed, _cfg())
    utterance = final["pending_proposal"].utterance
    for note in _REVERSIBILITY_NOTE.values():
        assert note not in utterance
