"""I2 — KB-retrieval beat + retriever-selector tests (execution §6/§8, §9).

Proves, WITHOUT network/creds, that:
  - the retriever selector returns the offline ``CachedRetriever`` in demo mode and
    a Moss-backed ``TimedRetriever`` is the live default (constructed lazily);
  - the ``CachedRetriever`` replays the recorded REAL Moss query result, exposing
    the recorded Moss IN-MEMORY ``last_runtime_ms`` (the panel number) + grounded
    KB Facts each carrying a Moss ``source_node_id``;
  - the negative-verification fact is asserted ONLY when grounded on BOTH sides
    (KB late-fee policy exists AND the page shows no fee), with polarity="absent";
  - ``MossKBBeat.from_cache`` produces the full beat offline (the demo-mode path).

The live Moss query path itself is covered by the skip-guarded
``clarion/retrieval/tests/test_retrieval.py`` (real index round-trip) — here we
only need the SELECTOR + the offline CACHED replay to be exercised every run.
"""

from __future__ import annotations

import pytest

from clarion.app.kb_beat import (
    KB_QUERY,
    MossKBBeat,
    build_negative_verification,
)
from clarion.app.runtime import CachedRetriever, select_kb_retriever
from clarion.contracts.ports import Retriever
from clarion.contracts.state import Fact


@pytest.mark.asyncio
async def test_selector_demo_mode_returns_cached_offline():
    """In demo mode the selector returns an offline ``CachedRetriever`` (no
    network) that satisfies the frozen ``Retriever`` ABC."""
    r = await select_kb_retriever(demo_mode=True)
    assert isinstance(r, CachedRetriever)
    assert isinstance(r, Retriever)
    # The recorded Moss in-memory number is present (the panel latency number).
    assert r.last_runtime_ms is not None
    assert r.index == "clarion-kb"


@pytest.mark.asyncio
async def test_cached_retriever_replays_grounded_kb_facts():
    """The cached retriever replays the recorded REAL Moss facts — each citable
    with a Moss source_node_id (the grounding invariant survives caching)."""
    r = CachedRetriever()
    facts = await r.query(KB_QUERY, k=3)
    assert facts, "cached Moss replay returned no facts"
    assert all(isinstance(f, Fact) for f in facts)
    # Grounding invariant: every replayed fact carries a Moss doc id.
    assert all(f.source_node_id for f in facts)
    assert all(str(f.source_node_id).startswith("clarion-kb::") for f in facts)
    # The late-fee policy passage is present (the beat's load-bearing KB fact).
    assert any("late fee" in f.value.lower() for f in facts)
    # retrieved_at stamped fresh on replay.
    assert all(f.retrieved_at > 0 for f in facts)


def test_negative_verification_only_when_grounded_both_sides():
    """The negative fact is asserted ONLY when a late-fee KB passage grounds it AND
    the page shows no fee — never a vibes-based negative (foundation §1)."""
    kb = [
        Fact(value="## Late fees: a flat $15 late fee ...", source_node_id="clarion-kb::lf"),
        Fact(value="## AutoPay: never incur a late fee ...", source_node_id="clarion-kb::ap"),
    ]
    # Page shows NO fee + KB grounds the policy → assertable negative.
    neg = build_negative_verification(kb, page_late_fee_present=False)
    assert neg is not None
    assert neg.polarity == "absent"
    assert neg.verified is True
    assert neg.source_node_id == "clarion-kb::lf"  # cites the late-fee passage
    assert "not present" in neg.value.lower()

    # Page DOES show a fee → the negative is NOT assertable (honest: no claim).
    assert build_negative_verification(kb, page_late_fee_present=True) is None

    # No late-fee KB passage to ground against → not assertable.
    no_lf = [Fact(value="## AutoPay only", source_node_id="clarion-kb::ap")]
    assert build_negative_verification(no_lf, page_late_fee_present=False) is None


def test_kb_beat_from_cache_is_complete_offline():
    """``MossKBBeat.from_cache`` produces the full beat offline (the demo path):
    grounded facts + the recorded in-memory number + the negative fact."""
    beat = MossKBBeat.from_cache(page_late_fee_present=False)
    assert beat.live is False
    assert "cached" in beat.source_label.lower()
    assert beat.facts and all(f.source_node_id for f in beat.facts)
    # The panel number is the recorded Moss IN-MEMORY runtime (not the wall-clock).
    assert beat.runtime_ms is not None
    assert beat.negative_fact is not None
    assert beat.negative_fact.polarity == "absent"


@pytest.mark.asyncio
async def test_from_live_reads_in_memory_runtime_ms_not_wall_clock():
    """``from_live`` reads the Moss IN-MEMORY ``last_runtime_ms`` off the inner
    retriever (R-Moss guidance), distinct from the TimedRetriever wall-clock."""

    class _FakeMoss(Retriever):
        # Mimics MossRetriever's panel surface.
        index = "clarion-kb"
        last_runtime_ms = 2  # the in-memory number

        async def query(self, q: str, *, k: int = 5) -> list[Fact]:
            return [
                Fact(value="## Late fees: flat $15", source_node_id="clarion-kb::lf"),
                Fact(value="## AutoPay terms", source_node_id="clarion-kb::ap"),
            ]

    from clarion.instrument import TimedRetriever

    timed = TimedRetriever(_FakeMoss())
    beat = await MossKBBeat.from_live(timed, page_late_fee_present=False)
    assert beat.live is True
    # The panel number is the inner in-memory 2 ms, NOT the wall-clock.
    assert beat.runtime_ms == 2
    assert beat.wall_ms is not None and beat.wall_ms >= 0
    assert beat.negative_fact is not None and beat.negative_fact.polarity == "absent"
