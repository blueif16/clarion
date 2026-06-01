"""``MossRetriever`` — the live ``Retriever`` (Moss + Gemini), execution §2.2 GROUND.

``query(q, k)`` → ranked ``list[Fact]``, each carrying:
  - ``source_node_id`` = the Moss doc id (the passage ref) → grounding invariant,
    so a surfaced fact is always citable and MAY be spoken (foundation §1).
  - ``retrieved_at`` = Unix epoch seconds, stamped at retrieval → the §8 latency
    meter. (The ``instrument.TimedRetriever`` wraps this and also records
    wall-clock ms; ``MossRetriever`` additionally exposes ``last_query_ms`` from
    the runtime's own ``time_taken_ms`` so the panel can show the *local* number.)

The first query lazily ``load_index``-es the Moss index into the in-memory runtime
so subsequent queries run locally in ~1-10 ms (the latency-meter beat). The query
text is embedded with Gemini (``gemini-embedding-001``) because the index is a
custom-embedding index — see ``moss_client`` / ``ingest_gemini`` docstrings.

This is what ``TimedRetriever`` wraps in the hero run, replacing ``FakeRetriever``.
"""

from __future__ import annotations

import os
import time

from clarion.contracts.ports import Retriever
from clarion.contracts.state import Fact

from clarion.retrieval.ingest_gemini import GeminiEmbedder
from clarion.retrieval.moss_client import MossClient

_DEFAULT_INDEX = os.environ.get("MOSS_INDEX", "clarion-kb")


class MossRetriever(Retriever):
    """Live grounding source backed by Moss + Gemini query embeddings.

    Args:
        moss:     a ``MossClient`` (defaults to one built from env creds).
        embedder: a ``GeminiEmbedder`` (defaults to one built from env creds).
        index:    the Moss index to search.
        alpha:    hybrid blend (1.0 = pure semantic, 0.0 = pure keyword). For a
                  custom-embedding index the semantic side uses the supplied
                  Gemini vector; default 0.8 leans semantic.
    """

    def __init__(
        self,
        *,
        moss: MossClient | None = None,
        embedder: GeminiEmbedder | None = None,
        index: str = _DEFAULT_INDEX,
        alpha: float = 0.8,
    ) -> None:
        self._moss = moss or MossClient()
        self._embedder = embedder or GeminiEmbedder()
        self._index = index
        self._alpha = alpha
        self._loaded = False
        self._last_query_ms: float | None = None
        self._last_runtime_ms: int | None = None

    @property
    def index(self) -> str:
        return self._index

    @property
    def last_query_ms(self) -> float | None:
        """Wall-clock ms of the most recent ``query`` (embed + search)."""
        return self._last_query_ms

    @property
    def last_runtime_ms(self) -> int | None:
        """The runtime's own ``time_taken_ms`` for the most recent search — the
        pure in-memory vector-search number (no embed/RPC), for the panel."""
        return self._last_runtime_ms

    async def _ensure_loaded(self) -> None:
        if not self._loaded:
            await self._moss.load_index(self._index)
            self._loaded = True

    async def query(self, q: str, *, k: int = 5) -> list[Fact]:
        """Embed the query with Gemini, run the local Moss search, map hits →
        ranked grounded ``Fact``s (each with a ``source_node_id`` + ``retrieved_at``)."""
        await self._ensure_loaded()

        t0 = time.perf_counter()
        vec = (await self._embedder.embed([q]))[0]
        res = await self._moss.search(
            self._index, q, top_k=k, embedding=vec, alpha=self._alpha
        )
        self._last_query_ms = (time.perf_counter() - t0) * 1000.0
        self._last_runtime_ms = res.time_taken_ms

        now = time.time()
        facts: list[Fact] = []
        for hit in res.hits:
            facts.append(
                Fact(
                    value=hit.text,
                    # The Moss doc id is the citable source handle → grounding.
                    source_node_id=hit.id,
                    polarity="present",
                    verified=False,
                    retrieved_at=now,
                )
            )
        return facts


__all__ = ["MossRetriever"]
