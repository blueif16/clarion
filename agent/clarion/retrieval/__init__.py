"""Clarion live retrieval stack — the REAL Retriever + Ingest + Memory.

Replaces the ``fakes`` (``FakeRetriever`` / ``FakeIngest`` / ``FakeMemory``) with
live providers behind the same frozen ports:

  - ``MossRetriever``    (retriever_moss.py) — Moss local search, Gemini query
    embeddings → ranked ``Fact``s with ``source_node_id`` + ``retrieved_at``.
  - ``GeminiMossIngest`` (ingest_gemini.py)  — Gemini parse/embed → Moss upsert
    → ``list[Passage]``.
  - ``MossMemory``       (memory_moss.py)    — per-user durable fact store on Moss.
  - ``MossClient`` / ``MossDoc`` / ``MossHit`` / ``MossSearch`` (moss_client.py)
    — the thin live wire seam onto the Moss service.

Provider SDK imports (``moss``, ``google-genai``) live ONLY in these modules,
never in ``contracts/`` or the kernel (foundation §6 / execution §18). All clients
are lazily constructed so the package is importable without creds.
"""

from clarion.retrieval.ingest_gemini import GeminiEmbedder, GeminiMossIngest
from clarion.retrieval.memory_moss import MossMemory
from clarion.retrieval.moss_client import (
    MossClient,
    MossDoc,
    MossHit,
    MossSearch,
    builtin_embed_model,
)
from clarion.retrieval.retriever_moss import MossRetriever

__all__ = [
    "MossClient",
    "MossDoc",
    "MossHit",
    "MossSearch",
    "MossRetriever",
    "GeminiMossIngest",
    "GeminiEmbedder",
    "MossMemory",
    "builtin_embed_model",
]
