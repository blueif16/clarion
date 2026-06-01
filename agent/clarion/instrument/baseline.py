"""Cold-RAG baseline — the greyed-out comparison number on the latency meter.

The execution §8 latency meter shows two numbers side by side:
  - The live Moss number (``retrieval_ms``) — the real, warm retrieval latency.
  - The greyed cold-RAG baseline (``baseline_ms``) — what local FAISS/Pinecone
    would cost on a cold cache, per prior brief / execution §8 (~300–400ms).

``COLD_RAG_BASELINE_MS`` is the configurable constant used by the panel.
``SlowFakeRetriever`` simulates the cold-RAG timing for test and demo use so
the panel can show the contrast even before a real Moss adapter is wired.

This module is **pure**: stdlib only + the frozen contracts. No provider SDKs.
"""

from __future__ import annotations

import asyncio
import time

from clarion.contracts.ports import Retriever
from clarion.contracts.state import Fact

# ---------------------------------------------------------------------------
# The configurable baseline constant (execution §8 "~300–400ms")
# ---------------------------------------------------------------------------

#: The greyed cold-RAG baseline latency in milliseconds.  Sourced from the
#: prior research brief (local FAISS/Pinecone p50 ~300–400ms). This is the
#: ``baseline_ms`` field that drives the panel's struck-through reference line.
COLD_RAG_BASELINE_MS: float = 340.0


# ---------------------------------------------------------------------------
# SlowFakeRetriever — simulates cold-RAG timing
# ---------------------------------------------------------------------------


class SlowFakeRetriever(Retriever):
    """A ``Retriever`` that injects a configurable sleep to simulate cold-RAG
    latency. Used in tests and demo scenarios to produce a ``retrieval_ms``
    that is visibly larger than the warm Moss number, making the panel contrast
    legible without a real cold-RAG stack.

    The underlying corpus delegates to a ``FakeRetriever``-style dict lookup so
    it returns deterministic, grounded facts — not garbage. If ``corpus`` is
    omitted, it falls back to a single echo fact (same as FakeRetriever).

    Args:
        delay_ms: Simulated latency in milliseconds (default: COLD_RAG_BASELINE_MS).
        corpus:   Optional ``{needle: facts}`` dict for deterministic results.
    """

    def __init__(
        self,
        delay_ms: float = COLD_RAG_BASELINE_MS,
        corpus: dict[str, list[Fact]] | None = None,
    ) -> None:
        self.delay_ms = delay_ms
        self.corpus = corpus or {}

    async def query(self, q: str, *, k: int = 5) -> list[Fact]:
        """Sleep for ``delay_ms`` milliseconds, then return deterministic facts."""
        await asyncio.sleep(self.delay_ms / 1000.0)
        now = time.time()
        for needle, facts in self.corpus.items():
            if needle.lower() in q.lower():
                result = facts[:k]
                # Stamp retrieved_at for facts that carry the default.
                return [
                    f.model_copy(update={"retrieved_at": now}) if f.retrieved_at == 0.0 else f
                    for f in result
                ]
        # Deterministic fallback echo fact.
        return [
            Fact(
                value=f"cold-rag result for: {q}",
                source_node_id=f"cold-rag::{abs(hash(q)) % 10_000}",
                verified=True,
                retrieved_at=now,
            )
        ][:k]


__all__ = ["COLD_RAG_BASELINE_MS", "SlowFakeRetriever"]
