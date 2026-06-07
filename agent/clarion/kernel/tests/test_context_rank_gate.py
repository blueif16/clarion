"""The ContextRanker node-count GATE, proven through the public ``build_kernel``
API: the ranker fires only when the live map has >= ``rank_min_nodes`` nodes
(win-or-free — never pay the embed on a small page where it wouldn't pay off).

Network-free: a spy ranker + scripted fakes, ZERO provider SDK."""

from __future__ import annotations

import uuid

import pytest

from clarion.contracts.ports import Actuator, ContextRanker, Retriever
from clarion.contracts.state import (
    Action,
    AxNode,
    Fact,
    Observation,
    PageDiff,
    SelectorMap,
)
from clarion.fakes import FakeReasoner
from clarion.kernel.graph import build_kernel, seed_state


def _cfg() -> dict:
    return {"configurable": {"thread_id": str(uuid.uuid4())}}


class _ScriptedRetriever(Retriever):
    async def query(self, q: str, *, k: int = 5):  # noqa: ARG002
        return []


class _NodesActuator(Actuator):
    """A page with exactly ``n`` interactive nodes (to sit either side of the gate)."""

    def __init__(self, n: int) -> None:
        self._n = n

    def _map(self) -> SelectorMap:
        return SelectorMap(
            nodes={
                i: AxNode(index=i, role="link", name=f"Link {i}", node_id=f"n-{i}")
                for i in range(self._n)
            },
            token_estimate=10,
        )

    async def perceive(self) -> SelectorMap:
        return self._map()

    async def act(self, action: Action) -> Observation:  # noqa: ARG002
        return Observation(selector_map=self._map(), success=True)

    async def diff(self, before: SelectorMap, after: SelectorMap) -> PageDiff:  # noqa: ARG002
        return PageDiff()


class _SpyRanker(ContextRanker):
    """Records calls; returns a 1-node slice (a valid subset of live indices)."""

    def __init__(self) -> None:
        self.calls = 0

    async def rank(self, intent, page, facts, k):  # noqa: ANN001, ARG002
        self.calls += 1
        first = sorted(page.nodes)[0]
        return SelectorMap(
            nodes={first: page.nodes[first]}, token_estimate=page.token_estimate
        )


async def _run(node_count: int, gate: int) -> int:
    actuator = _NodesActuator(node_count)
    spy = _SpyRanker()
    graph = build_kernel(
        FakeReasoner(), _ScriptedRetriever(), actuator, ranker=spy, rank_min_nodes=gate
    )
    seed = seed_state(goal="open something")
    seed["page_index"] = await actuator.perceive()
    await graph.ainvoke(seed, _cfg())
    return spy.calls


@pytest.mark.asyncio
async def test_ranker_skipped_below_gate() -> None:
    assert await _run(node_count=5, gate=10) == 0  # small page → full map, no embed


@pytest.mark.asyncio
async def test_ranker_fires_at_or_above_gate() -> None:
    assert await _run(node_count=12, gate=10) == 1  # big page → ranker fires once
