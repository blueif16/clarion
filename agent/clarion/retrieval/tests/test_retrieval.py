"""Tests for the live retrieval stack (Moss + Gemini).

Two tiers:
  - **pure unit** — no creds, no network: the adapters import, construct, and
    satisfy their frozen ABCs (the contract guarantee).
  - **live integration** — skip-guarded on creds + the ``moss`` SDK: ingest the
    demo KB doc → query it → assert ranked grounded Facts (with ``source_node_id``)
    come back and query latency is measured (the §8 latency-meter number).

The live test creates a throwaway index, asserts, and tears it down.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

from clarion.contracts.ports import Ingest, Memory, Retriever
from clarion.contracts.state import Fact, Passage, Profile
from clarion.instrument import TimedRetriever
from clarion.retrieval import (
    GeminiEmbedder,
    GeminiMossIngest,
    MossClient,
    MossMemory,
    MossRetriever,
)

load_dotenv(Path(__file__).resolve().parents[3] / ".env")

_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "northwind_policy.md"

_HAS_CREDS = bool(os.environ.get("MOSS_PROJECT_ID")) and bool(
    os.environ.get("MOSS_PROJECT_KEY")
) and bool(os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY"))

try:
    import moss as _moss  # noqa: F401
    import moss_core as _moss_core  # noqa: F401

    _HAS_SDK = True
except Exception:  # noqa: BLE001
    _HAS_SDK = False

_skip_no_creds = pytest.mark.skipif(
    not (_HAS_CREDS and _HAS_SDK),
    reason="live Moss test needs MOSS_PROJECT_ID/KEY + GOOGLE_API_KEY + the moss SDK",
)


def live(fn):
    """Mark a test as a network-dependent live Moss integration test.

    Applies BOTH the deselectable ``live`` marker (so the default regression
    suite excludes these via ``addopts = -m "not live"`` — they depend on an
    intermittently-available sponsor service and must not make the gate flaky)
    AND the creds/SDK skip-guard. Run them on demand with ``pytest -m live``.
    """
    return pytest.mark.live(_skip_no_creds(fn))

# A unique index per test run so reruns / parallel runs never collide.
_LIVE_INDEX = f"clarion-test-{os.getpid()}"


# --------------------------------------------------------------------------
# Pure unit — no creds, no network
# --------------------------------------------------------------------------
def test_adapters_satisfy_abcs() -> None:
    """The live adapters are concrete implementations of the frozen ports and
    are constructible without creds (lazy clients)."""
    r = MossRetriever(index="unit")
    ing = GeminiMossIngest(index="unit")
    mem = MossMemory(user_id="unit")

    assert isinstance(r, Retriever)
    assert isinstance(ing, Ingest)
    assert isinstance(mem, Memory)
    # MossRetriever is wrappable by the §8 latency meter.
    timed = TimedRetriever(r)
    assert isinstance(timed, Retriever)
    assert timed.last_query_ms is None


def test_construct_without_creds_is_lazy() -> None:
    """No network / SDK touch at construction — the client builds on first use."""
    c = MossClient(project_id="x", project_key="y")
    assert c.project_id == "x"
    assert c._sdk is None  # not built yet


def test_chunker_splits_markdown_sections() -> None:
    from clarion.retrieval.ingest_gemini import _chunk_text

    text = _FIXTURE.read_text()
    chunks = _chunk_text(text)
    # The demo doc has multiple ## sections → multiple citable passages.
    assert len(chunks) >= 3
    assert any("late fee" in c.lower() for c in chunks)
    assert any("autopay" in c.lower() for c in chunks)


# --------------------------------------------------------------------------
# Live integration — skip-guarded
# --------------------------------------------------------------------------
@live
async def test_live_health_probe() -> None:
    ok, detail = await MossClient().health()
    assert ok, f"Moss control plane not reachable: {detail}"


@live
async def test_live_ingest_query_roundtrip() -> None:
    """Ingest the demo KB doc into a throwaway index, then query it: assert
    ranked grounded Facts with source refs + a measured query latency."""
    moss = MossClient()
    embedder = GeminiEmbedder()
    ingest = GeminiMossIngest(moss=moss, embedder=embedder, index=_LIVE_INDEX)
    retriever = MossRetriever(moss=moss, embedder=embedder, index=_LIVE_INDEX)

    try:
        # --- ingest ---
        passages = await ingest.ingest(_FIXTURE.read_text())
        assert passages, "ingest returned no passages"
        assert all(isinstance(p, Passage) for p in passages)
        assert all(p.ref for p in passages), "every passage must carry a citable ref"

        # --- query (wrapped in the §8 latency meter) ---
        timed = TimedRetriever(retriever)
        facts = await timed.query("how much is the late fee?", k=3)

        assert facts, "query returned no facts"
        assert all(isinstance(f, Fact) for f in facts)
        # Grounding invariant: every surfaced fact MUST be citable.
        assert all(f.source_node_id for f in facts), "facts must carry source_node_id"
        # retrieved_at stamped (drives the latency meter).
        assert all(f.retrieved_at > 0 for f in facts)
        # The latency meter measured a positive query time.
        assert timed.last_query_ms is not None and timed.last_query_ms > 0
        # The runtime's own in-memory search time is available for the panel.
        assert retriever.last_runtime_ms is not None
        # The top hit should be the late-fee passage.
        assert "late fee" in facts[0].value.lower()

        print(
            f"\n[LIVE] ingest={len(passages)} passages; query latency "
            f"{timed.last_query_ms:.2f}ms (runtime search "
            f"{retriever.last_runtime_ms}ms); top source_node_id="
            f"{facts[0].source_node_id}"
        )
    finally:
        try:
            await moss.delete_index(_LIVE_INDEX)
        except Exception:  # noqa: BLE001
            pass


@live
async def test_live_memory_write_read() -> None:
    """A written verified fact reads back from the per-user profile (R3)."""
    mem = MossMemory(user_id=f"test-{os.getpid()}")
    from clarion.retrieval.memory_moss import _index_for

    idx = _index_for(f"test-{os.getpid()}")
    try:
        fact = Fact(
            value="The customer prefers AutoPay enrollment.",
            source_node_id="clarion-kb::autopay",
            verified=True,
        )
        await mem.write(fact)
        profile = await mem.read_profile(f"test-{os.getpid()}")
        assert isinstance(profile, Profile)
        assert any("autopay" in f.value.lower() for f in profile.facts)
        written = next(f for f in profile.facts if "autopay" in f.value.lower())
        assert written.verified is True
        assert written.source_node_id == "clarion-kb::autopay"
    finally:
        try:
            await MossClient().delete_index(idx)
        except Exception:  # noqa: BLE001
            pass
