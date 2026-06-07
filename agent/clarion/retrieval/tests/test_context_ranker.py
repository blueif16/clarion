"""Offline tests for the semantic ``EmbeddingContextRanker`` (de-hardcoded
top-K). A deterministic FAKE embedder drives the cosine math, so the gate stays
network-free — fastembed / a real model is never imported here."""

from __future__ import annotations

import pytest

from clarion.contracts.state import AxNode, Fact, SelectorMap
from clarion.retrieval.context_ranker import EmbeddingContextRanker, _signature


class _FakeEmbedder:
    """Returns a fixed vector per text (keyed by exact string). Unknown text → zero
    vector. Records calls; can be told to raise or to return a wrong-length list to
    exercise the fail-open paths."""

    def __init__(self, table, *, raise_on_embed=False, drop_one=False):
        self.table = table
        self.raise_on_embed = raise_on_embed
        self.drop_one = drop_one
        self.calls: list[list[str]] = []

    async def embed(self, texts):
        self.calls.append(list(texts))
        if self.raise_on_embed:
            raise RuntimeError("embed boom")
        vecs = [self.table.get(t, [0.0, 0.0, 0.0]) for t in texts]
        if self.drop_one:
            vecs = vecs[:-1]  # malformed: one short
        return vecs


def _node(idx, role, name, node_id):
    return AxNode(index=idx, role=role, name=name, node_id=node_id)


def _page():
    nodes = {
        1: _node(1, "searchbox", "Search", "n-1"),
        2: _node(2, "link", "Food assistance", "n-2"),
        3: _node(3, "link", "Taxes", "n-3"),
        4: _node(4, "link", "Footer", "n-4"),
        5: _node(5, "link", "Low relevance grounded", "n-5"),
    }
    return SelectorMap(nodes=nodes, token_estimate=100)


_INTENT = "open food assistance"
# Descending relevance so top-K is unambiguous: node2 (1.0) > node3 (0.6) > others.
_TABLE = {
    _INTENT: [1.0, 0.0, 0.0],
    _signature(_node(1, "searchbox", "Search", "n-1")): [0.0, 1.0, 0.0],          # ~0
    _signature(_node(2, "link", "Food assistance", "n-2")): [1.0, 0.0, 0.0],       # 1.0
    _signature(_node(3, "link", "Taxes", "n-3")): [0.6, 0.8, 0.0],                 # 0.6
    _signature(_node(4, "link", "Footer", "n-4")): [0.1, 0.0, 0.99],              # ~0.1
    _signature(_node(5, "link", "Low relevance grounded", "n-5")): [0.0, 0.0, 1.0],  # ~0
}


async def test_noop_when_page_fits_k():
    ranker = EmbeddingContextRanker(_FakeEmbedder(_TABLE))
    page = _page()
    out = await ranker.rank(_INTENT, page, [], k=10)
    assert set(out.nodes) == set(page.nodes)  # nothing dropped


async def test_selects_top_k_by_meaning():
    ranker = EmbeddingContextRanker(_FakeEmbedder(_TABLE))
    out = await ranker.rank(_INTENT, _page(), [], k=2)
    # node2 (Food assistance, 1.0) + node3 (Taxes, 0.6) — the two most relevant.
    assert set(out.nodes) == {2, 3}
    # sub-map keeps the SAME live indices/nodes
    assert out.nodes[2].name == "Food assistance"


async def test_recall_keeps_grounded_fact_node_even_if_low_score():
    ranker = EmbeddingContextRanker(_FakeEmbedder(_TABLE))
    # node 5 ('n-5') is a grounded-fact source but scores ~0 → must still survive.
    facts = [Fact(value="x", source_node_id="n-5")]
    out = await ranker.rank(_INTENT, _page(), facts, k=2)
    assert 5 in out.nodes  # recall guarantee
    assert 2 in out.nodes  # top-relevant still chosen
    assert len(out.nodes) == 2


async def test_returned_indices_are_subset_of_live():
    ranker = EmbeddingContextRanker(_FakeEmbedder(_TABLE))
    page = _page()
    out = await ranker.rank(_INTENT, page, [], k=3)
    assert set(out.nodes).issubset(set(page.nodes))
    assert len(out.nodes) == 3


async def test_fail_open_on_embed_error_returns_full_map():
    ranker = EmbeddingContextRanker(_FakeEmbedder(_TABLE, raise_on_embed=True))
    page = _page()
    out = await ranker.rank(_INTENT, page, [], k=2)
    assert set(out.nodes) == set(page.nodes)  # never pruned on error


async def test_fail_open_on_malformed_vectors_returns_full_map():
    ranker = EmbeddingContextRanker(_FakeEmbedder(_TABLE, drop_one=True))
    page = _page()
    out = await ranker.rank(_INTENT, page, [], k=2)
    assert set(out.nodes) == set(page.nodes)


async def test_intent_is_first_embedded_text():
    fake = _FakeEmbedder(_TABLE)
    ranker = EmbeddingContextRanker(fake)
    await ranker.rank(_INTENT, _page(), [], k=2)
    # one embed call: [intent] + one signature per node, intent first.
    assert fake.calls and fake.calls[0][0] == _INTENT
    assert len(fake.calls[0]) == 1 + len(_page().nodes)
