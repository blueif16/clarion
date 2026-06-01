"""I2 — the KB-retrieval beat: LIVE Moss grounding + the negative-verification fact.

This is the §6/§8 latency-meter + negative-verification beat — the SECOND kind of
grounded fact (KB facts), distinct from the PAGE facts read off the live AXTree:

  - PAGE facts (amount $84.32 / payee / due / confirmation #) come from the live
    AXTree/DOM — the actuator already grounds those.
  - KB facts (late-fee policy, autopay terms) are retrieved from MOSS (the ingested
    Northwind policy). THIS module owns that beat.

What it produces, from the REAL Moss query result:
  - the grounded KB ``Fact``s, each carrying its Moss ``source_node_id`` (the doc
    id → the grounding/citation handle).
  - the Moss IN-MEMORY ``last_runtime_ms`` (R-Moss's guidance: the 0-1ms in-memory
    vector-search number, NOT the wall-clock that includes the Gemini embed RPC) —
    the latency-meter number the panel shows beside ``COLD_RAG_BASELINE_MS``.
  - a NEGATIVE-VERIFICATION ``Fact`` (``polarity="absent"``) that cross-references
    the PAGE (no late fee shown on this bill) against the KB (a late-fee policy DOES
    exist): "no late fee currently applied on this bill [verified: not present]".
    This is the foundation §1 epistemic clause — asserting a *negative* only when
    BOTH sides are grounded (the KB says fees can exist; the page says none here).

Two sources, honestly distinguished:
  - LIVE: ``MossKBBeat.from_live`` queries the live ``MossRetriever`` over the
    prebuilt ``clarion-kb`` index — the real sub-ms in-memory number.
  - CACHED (offline demo): ``MossKBBeat.from_cache`` replays the recorded result
    from ``app/fixtures/hero_moss_kb.json`` (captured by ``record_moss_fixture``)
    — the SAME real passages + the SAME real recorded in-memory number, no network.

This module OWNS ``clarion/app/``; it imports the retrieval/instrument adapters
read-only and never modifies them.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Optional

from clarion.contracts.state import Fact

MOSS_FIXTURE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "fixtures", "hero_moss_kb.json"
)

# The KB query the LOCATE beat fires at Moss (the late-fee / autopay policy).
KB_QUERY = "What is the late fee policy and does autopay avoid it?"


def _fact_from_dict(d: dict) -> Fact:
    return Fact(
        value=d["value"],
        source_node_id=d.get("source_node_id"),
        polarity=d.get("polarity", "present"),  # type: ignore[arg-type]
        verified=bool(d.get("verified", False)),
        retrieved_at=time.time(),
    )


def build_negative_verification(
    kb_facts: list[Fact],
    *,
    page_late_fee_present: bool,
) -> Optional[Fact]:
    """The negative-verification fact (foundation §1, execution §3.2 REVIEW).

    Cross-references the KB (a late-fee policy EXISTS — grounded with a Moss
    source) against the live PAGE (``page_late_fee_present`` — whether any late
    fee is actually shown on THIS bill). Asserts the negative ONLY when the KB
    side is grounded AND the page shows no fee — never a vibes-based negative.

    Returns a ``polarity="absent"`` ``Fact`` citing the Moss late-fee passage, or
    ``None`` if the KB has no late-fee passage to ground against (honest: no
    grounding → no assertion).
    """
    late_fee_passage = next(
        (f for f in kb_facts if "late fee" in f.value.lower()), None
    )
    if late_fee_passage is None or page_late_fee_present:
        # Either we cannot ground "a late fee can exist" (no KB passage), or the
        # page DOES show a fee — in both cases the negative is not assertable.
        return None
    return Fact(
        value=(
            "No late fee currently applied on this bill [verified: not present] "
            "— the Northwind late-fee policy exists (KB) but no fee is shown on "
            "this statement (page)."
        ),
        # Grounded in BOTH: the Moss policy doc id (KB side) is the citable handle.
        source_node_id=late_fee_passage.source_node_id,
        polarity="absent",
        verified=True,
        retrieved_at=time.time(),
    )


@dataclass
class MossKBBeat:
    """One KB-retrieval beat's result — the facts + the panel latency number.

    Attributes:
        facts:           the grounded KB Facts (Moss source_node_ids).
        negative_fact:   the negative-verification Fact (may be None if unground-
                         able — reported honestly by the harness).
        runtime_ms:      the Moss IN-MEMORY ``last_runtime_ms`` (the panel number).
        wall_ms:         the wall-clock query ms (embed RPC + search) — context.
        live:            True if this came from a live Moss query; False if cached.
        index:           the Moss index queried.
        query:           the KB query text.
    """

    facts: list[Fact]
    negative_fact: Optional[Fact]
    runtime_ms: Optional[float]
    wall_ms: Optional[float]
    live: bool
    index: str
    query: str

    @property
    def source_label(self) -> str:
        return "live Moss" if self.live else "cached Moss (offline demo)"

    @classmethod
    async def from_live(
        cls,
        retriever,
        *,
        query: str = KB_QUERY,
        page_late_fee_present: bool = False,
    ) -> "MossKBBeat":
        """Query the LIVE Moss retriever (wrapped or bare) for the KB facts.

        ``retriever`` may be a ``TimedRetriever(MossRetriever)`` — we read the
        Moss in-memory ``last_runtime_ms`` off the inner ``MossRetriever`` (R-Moss
        guidance: the in-memory number, not the wall-clock) and the wall-clock off
        the ``TimedRetriever`` when present.
        """
        facts = await retriever.query(query, k=3)
        moss = _unwrap_moss(retriever)
        runtime_ms = getattr(moss, "last_runtime_ms", None) if moss else None
        wall_ms = getattr(retriever, "last_query_ms", None)
        index = getattr(moss, "index", os.environ.get("MOSS_INDEX", "clarion-kb"))
        neg = build_negative_verification(facts, page_late_fee_present=page_late_fee_present)
        return cls(
            facts=facts,
            negative_fact=neg,
            runtime_ms=float(runtime_ms) if runtime_ms is not None else None,
            wall_ms=float(wall_ms) if wall_ms is not None else None,
            live=True,
            index=index,
            query=query,
        )

    @classmethod
    def from_cache(
        cls,
        *,
        path: str = MOSS_FIXTURE_PATH,
        page_late_fee_present: bool = False,
    ) -> "MossKBBeat":
        """Replay the recorded Moss KB result from the app fixture — OFFLINE, no
        network. The recorded passages + the recorded in-memory number are REAL
        (captured by ``record_moss_fixture``); we never fabricate them."""
        if not os.path.exists(path):
            raise FileNotFoundError(
                f"Moss KB fixture not found at {path!r}. Record it first with the "
                f"live stack reachable:  .venv/bin/python -m clarion.app.record_moss_fixture"
            )
        with open(path) as f:
            rec = json.load(f)
        facts = [_fact_from_dict(d) for d in rec.get("facts", [])]
        neg = build_negative_verification(facts, page_late_fee_present=page_late_fee_present)
        return cls(
            facts=facts,
            negative_fact=neg,
            runtime_ms=rec.get("last_runtime_ms"),
            wall_ms=rec.get("last_query_ms"),
            live=False,
            index=rec.get("index", "clarion-kb"),
            query=rec.get("query", KB_QUERY),
        )


def _unwrap_moss(retriever):
    """Return the underlying object that exposes ``last_runtime_ms`` (the
    ``MossRetriever``), unwrapping a ``TimedRetriever`` if present. None if not a
    Moss-backed retriever."""
    if hasattr(retriever, "last_runtime_ms"):
        return retriever
    inner = getattr(retriever, "_inner", None)
    if inner is not None and hasattr(inner, "last_runtime_ms"):
        return inner
    return None


async def ensure_kb_index(
    *,
    index: Optional[str] = None,
    fixture_path: Optional[str] = None,
) -> tuple[str, bool]:
    """Ensure the Moss KB index exists; ingest the Northwind policy ONCE if missing.

    REUSES the prebuilt index if already present (does NOT re-ingest on every run —
    ingest builds a cloud index that takes time). Returns ``(index_name, ingested)``
    where ``ingested`` is True only if a build was performed this call.

    Live-only (touches Moss + Gemini). The runtime calls this once at LIVE startup;
    demo mode never calls it (offline).
    """
    from clarion.retrieval import GeminiEmbedder, GeminiMossIngest, MossClient

    index = index or os.environ.get("MOSS_INDEX", "clarion-kb")
    fixture_path = fixture_path or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "retrieval", "fixtures", "northwind_policy.md",
    )
    moss = MossClient()
    existing = {getattr(i, "name", None) for i in await moss.list_indexes()}
    if index in existing:
        return index, False
    # Build once (reused by every subsequent run).
    with open(fixture_path) as f:
        doc = f.read()
    ingest = GeminiMossIngest(moss=moss, embedder=GeminiEmbedder(), index=index)
    await ingest.ingest(doc)
    return index, True


__all__ = [
    "KB_QUERY",
    "MossKBBeat",
    "build_negative_verification",
    "ensure_kb_index",
    "MOSS_FIXTURE_PATH",
]
