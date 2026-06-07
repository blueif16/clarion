"""``GeminiMossIngest`` — the live ``Ingest`` adapter (execution §6, §15 R3).

Pipeline for ``ingest(doc)``:
  1. **parse** raw bytes/str → text. Plain text/markdown passes through; PDF (or
     other binary the LLM can read) is parsed with Gemini (``GEMINI_MODEL``) so we
     never need a separate parser SDK. (Unsiloed is the eventual parser per
     foundation §6; Gemini stands in here, behind the same ``Ingest`` ABC.)
  2. **chunk** the text into citable passages.
  3. **embed** — two paths (``MOSS_EMBED_MODEL`` via ``builtin_embed_model``):
     built-in (``moss-minilm``/``moss-mediumlm``) skips this step and lets the Moss
     runtime embed locally; else Gemini (``gemini-embedding-001``) custom vectors.
  4. **upsert** the chunks (+ vectors, when custom) into a Moss index, built with
     ``model_id=<built-in id>`` or ``"custom"``, and wait for the build.
  5. return ``list[Passage]`` — each ``ref`` is the Moss doc id, which becomes a
     spoken fact's ``source_node_id`` downstream (grounding invariant).

NEVER swaps models: the parse model is ``GEMINI_MODEL`` from env; the embedding
model is the current Gemini embedding model (``gemini-embedding-001``). Both come
from config, never hard-swapped to chase latency (standing rule).
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
from typing import Optional, Sequence

from clarion.contracts.ports import Ingest
from clarion.contracts.state import Passage

from clarion.retrieval.moss_client import MossClient, MossDoc, builtin_embed_model

# Current Gemini embedding model (text-embedding-004 is retired / 404s — verified
# live 2026-05-31). Output dimensionality is pinned so ingest + query vectors
# always match the index.
_EMBED_MODEL = "gemini-embedding-001"
_EMBED_DIM = 1536
# Default index every ingested doc lands in (overridable per call / env).
_DEFAULT_INDEX = os.environ.get("MOSS_INDEX", "clarion-kb")


class GeminiEmbedder:
    """Thin lazy wrapper over ``client.models.embed_content``.

    Built lazily so importing this module needs neither google-genai nor a key.
    """

    def __init__(self, *, api_key: str | None = None, model: str = _EMBED_MODEL,
                 dim: int = _EMBED_DIM) -> None:
        self._api_key = (
            api_key or os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
        )
        self._model = model
        self._dim = dim
        self._client = None

    @property
    def dim(self) -> int:
        return self._dim

    def _ensure(self):
        if self._client is None:
            if not self._api_key:
                raise RuntimeError(
                    "GOOGLE_API_KEY / GEMINI_API_KEY not set; cannot embed with Gemini."
                )
            from google import genai

            self._client = genai.Client(api_key=self._api_key)
        return self._client

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Batch-embed ``texts`` → list of vectors (length ``dim``)."""
        from google.genai import types

        client = self._ensure()
        model, dim = self._model, self._dim

        def _call() -> list[list[float]]:
            resp = client.models.embed_content(
                model=model,
                contents=list(texts),
                config=types.EmbedContentConfig(output_dimensionality=dim),
            )
            return [list(e.values) for e in resp.embeddings]

        return await asyncio.to_thread(_call)


def _chunk_text(text: str) -> list[str]:
    """Split into citable passages. Markdown ``##`` sections first; else split on
    blank lines; falls back to the whole doc as one chunk."""
    text = text.strip()
    if not text:
        return []
    # Prefer markdown section boundaries (keep the heading with its body).
    if re.search(r"^#{1,6}\s", text, flags=re.MULTILINE):
        parts = re.split(r"\n(?=#{1,6}\s)", text)
    else:
        parts = re.split(r"\n\s*\n", text)
    chunks = [p.strip() for p in parts if p.strip()]
    return chunks or [text]


class GeminiMossIngest(Ingest):
    """Live ``Ingest``: Gemini parse/embed → Moss upsert → ``list[Passage]``.

    Args:
        moss:  a ``MossClient`` (defaults to one built from env creds).
        embedder: a ``GeminiEmbedder`` (defaults to one built from env creds).
        index: the Moss index name to upsert into.
    """

    def __init__(
        self,
        *,
        moss: MossClient | None = None,
        embedder: GeminiEmbedder | None = None,
        index: str = _DEFAULT_INDEX,
    ) -> None:
        self._moss = moss or MossClient()
        # Built-in Moss model → no external embedder (the runtime embeds locally);
        # else the Gemini custom-embedding path. An explicitly injected embedder
        # wins (tests / callers that force the custom path).
        self._builtin = builtin_embed_model()
        self._embedder = embedder or (None if self._builtin else GeminiEmbedder())
        self._index = index

    @property
    def index(self) -> str:
        return self._index

    @property
    def embed_dim(self) -> int:
        return self._embedder.dim if self._embedder is not None else 0

    async def _parse(self, doc: bytes | str) -> str:
        """Raw doc → plain text. Text/markdown bytes decode directly; a PDF (or
        other binary) is parsed by Gemini so no separate parser SDK is needed."""
        if isinstance(doc, str):
            return doc
        # Try UTF-8 first (the common markdown/text case).
        try:
            return doc.decode("utf-8")
        except UnicodeDecodeError:
            pass
        # Binary (e.g. PDF) → Gemini parse to text.
        from google import genai
        from google.genai import types

        api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("GOOGLE_API_KEY not set; cannot Gemini-parse binary doc.")
        model = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")

        def _call() -> str:
            client = genai.Client(api_key=api_key)
            resp = client.models.generate_content(
                model=model,
                contents=[
                    types.Part.from_bytes(data=doc, mime_type="application/pdf"),
                    "Extract the full text content of this document as plain text. "
                    "Preserve section headings. Output only the text.",
                ],
            )
            return resp.text or ""

        return await asyncio.to_thread(_call)

    async def ingest(self, doc: bytes | str) -> list[Passage]:
        """Parse → chunk → Gemini-embed → upsert to Moss → return Passages."""
        text = await self._parse(doc)
        chunks = _chunk_text(text)
        if not chunks:
            return []

        # Built-in path: no vectors (Moss embeds at build time). Custom path: Gemini.
        if self._embedder is None:
            vectors: list = [None] * len(chunks)
        else:
            vectors = await self._embedder.embed(chunks)

        # Stable, content-derived ids so re-ingesting the same doc upserts in place.
        moss_docs: list[MossDoc] = []
        passages: list[Passage] = []
        for i, (chunk, vec) in enumerate(zip(chunks, vectors)):
            digest = hashlib.sha1(chunk.encode("utf-8")).hexdigest()[:12]
            ref = f"{self._index}::{digest}"
            heading = chunk.splitlines()[0][:80] if chunk.splitlines() else ""
            meta = {"chunk": str(i), "heading": heading}
            moss_docs.append(MossDoc(id=ref, text=chunk, metadata=meta, embedding=vec))
            passages.append(Passage(text=chunk, ref=ref, score=1.0, metadata=meta))

        # Upsert: create the index if it does not exist yet, else add_docs.
        await self._upsert(moss_docs)
        return passages

    async def _upsert(self, docs: Sequence[MossDoc]) -> None:
        """create_index on first ingest into a fresh index; add_docs thereafter.
        Either way, wait for the build so the index is queryable on return."""
        existing = {getattr(i, "name", None) for i in await self._moss.list_indexes()}
        if self._index in existing:
            res = await self._moss.add_docs(self._index, docs)
        else:
            # Built-in model id (moss-minilm/mediumlm) or the custom-vector index.
            res = await self._moss.create_index(
                self._index, docs, model_id=self._builtin or "custom"
            )
        job_id = getattr(res, "job_id", None)
        if job_id:
            await self._moss.wait_for_job(job_id)


__all__ = ["GeminiMossIngest", "GeminiEmbedder"]
