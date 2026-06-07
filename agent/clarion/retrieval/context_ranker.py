"""``EmbeddingContextRanker`` — the de-hardcoded successor to the deleted lexical
``kernel.graph._topk_slice``.

It returns the top-``k`` goal-relevant nodes PROPOSE hands the Reasoner, ranked by
MEANING (embedding cosine), never string overlap — so a control the goal needs
semantically (a "Food assistance" link for "help buying groceries") survives even
with zero shared words, which is exactly the case the lexical pre-rank pruned →
untargetable → give-up.

Why it speeds things up: a smaller candidate set is a smaller ``target_index``
enum (faster constrained decode) + less prefill. Why it is safe:

  - **Recall-first** — the source node of every grounded ``Fact`` is ALWAYS kept.
  - **Fail-open** — any embed hiccup (error / wrong length / empty) returns the
    FULL map unchanged, so the ranker can never be the reason the target is gone.

The embedder is INJECTED (any object with ``async embed(texts)->list[list[float]]``
— e.g. ``retrieval.ingest_gemini.GeminiEmbedder`` or a Moss in-process embedder),
so this module imports ZERO provider SDK. NOTE: the net latency win requires a FAST
embedder (a Moss-style in-process embed); a per-step cloud embed RPC can cost more
than the decode it saves — measure before enabling.
"""

from __future__ import annotations

import asyncio
import math
import os
from typing import Sequence

from clarion.contracts.ports import ContextRanker
from clarion.contracts.state import Fact, SelectorMap


class _Embedder:  # structural: anything with async embed(texts)->list[list[float]]
    async def embed(self, texts: Sequence[str]) -> list[list[float]]:  # pragma: no cover
        ...


def _cosine(a: list[float], b: list[float]) -> float:
    num = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return num / (na * nb)


def _signature(node) -> str:
    """The node's MEANING surface for embedding: role + accessible name + any active
    a11y state flags. NOT a value (values never leave the grounding path)."""
    flags = " ".join(k for k, v in (node.state or {}).items() if v)
    base = f"{node.role}: {node.name}".strip()
    return f"{base} [{flags}]" if flags else base


class EmbeddingContextRanker(ContextRanker):
    """Top-``k`` by embedding cosine to the intent, recall-first + fail-open."""

    def __init__(self, embedder: _Embedder) -> None:
        self._embedder = embedder

    async def rank(
        self, intent: str, page: SelectorMap, facts: list[Fact], k: int
    ) -> SelectorMap:
        nodes = page.nodes
        n = len(nodes)
        if k <= 0 or n <= k:
            return page  # nothing to gain — feed the full map.
        indices = sorted(nodes)

        # Recall guarantee: always keep the node every grounded fact was read from,
        # so whatever we retrieved stays targetable regardless of its rank.
        fact_node_ids = {f.source_node_id for f in facts if f.source_node_id}
        must = {i for i in indices if nodes[i].node_id in fact_node_ids}

        try:
            sigs = [_signature(nodes[i]) for i in indices]
            vecs = await self._embedder.embed([intent] + sigs)
        except Exception:  # noqa: BLE001 — NEVER prune on an embed error.
            return page
        if not vecs or len(vecs) != n + 1:
            return page  # fail open on a malformed embed response.

        q = vecs[0]
        scored = sorted(
            ((_cosine(q, vecs[j + 1]), i) for j, i in enumerate(indices)),
            key=lambda t: t[0],
            reverse=True,
        )
        chosen = set(must)
        for _score, i in scored:
            if len(chosen) >= k:
                break
            chosen.add(i)

        sub = {i: nodes[i] for i in sorted(chosen)}
        return SelectorMap(nodes=sub, token_estimate=page.token_estimate)


class LocalMiniLMEmbedder:
    """In-process MiniLM embedder via ``fastembed`` (ONNX runtime, NO torch, keyless).

    The SAME small-MiniLM model class as Moss's ``moss-minilm``, hosted LOCALLY — so
    we get the sub-10ms speed WITHOUT Moss's index/cloud round-trip (the Moss SDK
    only embeds inside a cloud-built index; it exposes no standalone embed). This is
    the fast embedder the ``EmbeddingContextRanker`` is meant to run on.

    LAZY: the model loads (and downloads once, ~90MB, to the HF cache) on first
    ``embed``, never at import — so the no-network test gate, which never calls it,
    stays fully offline. Model id via ``CLARION_RANK_EMBED_MODEL``."""

    _DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

    def __init__(self, model_name: str | None = None) -> None:
        self._model_name = (
            model_name
            or os.environ.get("CLARION_RANK_EMBED_MODEL")
            or self._DEFAULT_MODEL
        )
        self._model = None

    def _ensure(self):
        if self._model is None:
            from fastembed import TextEmbedding  # lazy: no import/download at module load

            self._model = TextEmbedding(model_name=self._model_name)
        return self._model

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Batch-embed ``texts`` → vectors. The blocking ONNX call runs in a worker
        thread so it never blocks the event loop."""

        def _run() -> list[list[float]]:
            model = self._ensure()
            return [[float(x) for x in v] for v in model.embed(list(texts))]

        return await asyncio.to_thread(_run)


__all__ = ["EmbeddingContextRanker", "LocalMiniLMEmbedder"]
