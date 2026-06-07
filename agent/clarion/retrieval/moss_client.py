"""Thin live Moss client — the single seam onto the Moss sponsor service.

This is the **only** module in ``retrieval/`` that knows the Moss wire protocol.
``retriever_moss`` / ``ingest_gemini`` / ``memory_moss`` all sit on top of it.

DISCOVERED MOSS API (live, probed 2026-05-31 with the project creds in agent/.env)
----------------------------------------------------------------------------------
Moss has two planes, both rooted at ``https://service.usemoss.dev``:

1. **Control plane** — ``POST /v1/manage`` (multiplexed by an ``action`` field).
   Auth: header ``x-project-key: moss_access_key_*`` + ``x-service-version: v1``;
   body carries ``projectId``. Live actions (from the service's own error reply)::

       validateCredentials, initUpload, startBuild, getJobStatus, addDocs,
       deleteDocs, pushLocalIndex, telemetry, reportUsage, getIndex,
       listIndexes, deleteIndex, getDocs, getIndexUrl

   ``GET /healthz`` / ``/readyz`` / ``/version`` are the liveness probes.

2. **Query plane** — the *runtime*. ``load_index(name)`` pulls the built index
   artifact (via ``getIndexUrl``) into memory; ``query`` then runs **locally**
   in ~1-10 ms (the latency-meter beat). A cloud query fallback exists at
   ``POST /query`` but at the time of writing it returns ``503`` (Moss-side
   upstream down), so the local path is the only working query path.

EMBEDDINGS — two selectable paths, via ``MOSS_EMBED_MODEL`` (``builtin_embed_model``):

  - **Built-in (default-recommended now)** — ``moss-minilm`` / ``moss-mediumlm``.
    Moss embeds text internally in its Rust runtime (no key, sub-ms, no external
    embed RPC). This path fetches the model from ``https://models.moss.link`` at
    ``load_index`` time; that host was TLS-broken (``WRONG_VERSION_NUMBER``) on
    2026-05-31 but is back to a clean TLS 1.3 handshake (re-probed 2026-06-06), so
    the built-in path is viable again. Docs carry NO ``embedding``; the index is
    built with ``model_id=<that built-in id>``.
  - **Custom (Gemini) — fallback** — when ``MOSS_EMBED_MODEL`` is unset/``gemini``
    we embed with Gemini (``gemini-embedding-001``) at ingest + query, mark the
    index ``model_id="custom"``, and the runtime never touches the model host.

The two are NOT mixable in one index — switching paths requires a rebuild.

The heavy lifting (HTTP to the control plane, pulling/holding the index artifact,
the in-memory vector search) lives in the official ``moss`` SDK + its native
``inferedge-moss-core`` runtime. This module is a thin, lazily-constructed,
async wrapper so the rest of ``retrieval/`` speaks plain Python objects and never
imports the SDK directly — mirroring the ``adapters/tts_vertex`` lazy-client seam.

Provider SDK import (``moss``) lives ONLY here, never in ``contracts/`` or the
kernel (foundation §6 / execution §18).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Optional, Sequence

# The Moss control-plane base; ``getMoss``-style overridable for local dev.
MANAGE_URL = os.environ.get("MOSS_MANAGE_URL", "https://service.usemoss.dev/v1/manage")
VERSION_URL = os.environ.get("MOSS_VERSION_URL", "https://service.usemoss.dev/version")
SERVICE_VERSION = "v1"

# Moss built-in embedding models (the runtime embeds locally — no external key,
# sub-ms — but must fetch the model from models.moss.link at load_index time).
_BUILTIN_EMBED_MODELS = {"moss-minilm", "moss-mediumlm"}


def builtin_embed_model() -> Optional[str]:
    """The configured Moss built-in embedding model id, or ``None`` for the Gemini
    custom-embedding path. Reads ``MOSS_EMBED_MODEL`` (e.g. ``moss-mediumlm``);
    unset / ``gemini`` / anything unrecognized → ``None`` (custom Gemini path).

    Consumed by ``ingest_gemini`` / ``retriever_moss`` / ``memory_moss`` so the
    embedding path is one config flip + a rebuild, with Gemini as the fallback."""
    m = os.environ.get("MOSS_EMBED_MODEL", "").strip().lower()
    return m if m in _BUILTIN_EMBED_MODELS else None


@dataclass
class MossDoc:
    """A document to upsert into a Moss index.

    ``embedding`` is the pre-computed Gemini vector (custom-embedding path). When
    ``None``, Moss would embed internally with ``moss-minilm`` (default path).
    """

    id: str
    text: str
    metadata: Optional[dict[str, str]] = None
    embedding: Optional[Sequence[float]] = None


@dataclass
class MossHit:
    """One ranked search result from Moss."""

    id: str
    text: str
    score: float
    metadata: Optional[dict[str, str]] = None


@dataclass
class MossSearch:
    """A ranked search response plus the runtime-reported timing."""

    hits: list[MossHit]
    query: str
    index_name: Optional[str]
    time_taken_ms: Optional[int]


class MossClient:
    """Thin async wrapper over the official ``moss`` SDK.

    Constructed lazily: ``__init__`` only resolves config (no network, no SDK
    import) so the module is importable in a contracts-only / no-creds env and
    the unit test can construct it without a live key. The SDK client + native
    runtime are built on first use in ``_ensure()``.

    Args:
        project_id:  ``MOSS_PROJECT_ID`` (a project UUID). Defaults to env.
        project_key: ``MOSS_PROJECT_KEY`` (``moss_*`` access key). Defaults to env.
    """

    DEFAULT_MODEL_ID = "moss-minilm"

    def __init__(
        self,
        *,
        project_id: str | None = None,
        project_key: str | None = None,
    ) -> None:
        self._project_id = project_id or os.environ.get("MOSS_PROJECT_ID")
        self._project_key = project_key or os.environ.get("MOSS_PROJECT_KEY")
        self._sdk = None  # built lazily in _ensure()
        self._loaded: set[str] = set()

    @property
    def project_id(self) -> Optional[str]:
        return self._project_id

    def _ensure(self):
        """Build the ``moss.MossClient`` SDK instance on first use (no I/O at
        import). Raises a clear error if creds or the SDK/runtime are absent."""
        if self._sdk is None:
            if not self._project_id or not self._project_key:
                raise RuntimeError(
                    "MOSS_PROJECT_ID / MOSS_PROJECT_KEY are not set; cannot "
                    "construct the Moss client."
                )
            try:
                from moss import MossClient as _SDK
            except Exception as e:  # noqa: BLE001
                raise RuntimeError(
                    "The 'moss' SDK (and native 'inferedge-moss-core' runtime) is "
                    "not installed. It is required for the live Moss query path. "
                    f"Import error: {e}"
                ) from e
            self._sdk = _SDK(self._project_id, self._project_key)
        return self._sdk

    # -- low-level control-plane probe (direct HTTP, no SDK) ----------------
    async def health(self) -> tuple[bool, str]:
        """Liveness probe of the control plane via the SDK's own httpx dep.

        Hits ``GET /version`` then ``listIndexes`` so the probe exercises both
        reachability *and* auth. Returns ``(ok, detail)`` without raising."""
        import httpx

        try:
            async with httpx.AsyncClient(timeout=20.0) as c:
                v = await c.get(VERSION_URL)
                ver = v.text[:120]
            indexes = await self.list_indexes()
            names = [getattr(i, "name", str(i)) for i in indexes]
            return True, f"version={ver} listIndexes ok ({len(names)} indexes: {names})"
        except Exception as e:  # noqa: BLE001
            return False, f"{type(e).__name__}: {str(e).splitlines()[0][:200]}"

    # -- mutations ----------------------------------------------------------
    async def create_index(
        self,
        name: str,
        docs: Sequence[MossDoc],
        *,
        model_id: str | None = None,
    ) -> Any:
        """Create + build an index from ``docs``. Returns the SDK MutationResult
        (carries ``job_id`` to poll with ``wait_for_job``)."""
        from moss import DocumentInfo

        sdk = self._ensure()
        # When any doc carries a precomputed embedding we MUST mark the index
        # 'custom' (the SDK auto-resolves this, but we make it explicit so the
        # broken moss-minilm model host is never touched at load time).
        has_emb = any(d.embedding is not None for d in docs)
        resolved = model_id or ("custom" if has_emb else self.DEFAULT_MODEL_ID)
        sdk_docs = [
            DocumentInfo(
                id=d.id,
                text=d.text,
                metadata=d.metadata,
                embedding=list(d.embedding) if d.embedding is not None else None,
            )
            for d in docs
        ]
        return await sdk.create_index(name, sdk_docs, resolved)

    async def add_docs(self, name: str, docs: Sequence[MossDoc]) -> Any:
        """Upsert ``docs`` into an existing index. Returns the MutationResult."""
        from moss import DocumentInfo, MutationOptions

        sdk = self._ensure()
        sdk_docs = [
            DocumentInfo(
                id=d.id,
                text=d.text,
                metadata=d.metadata,
                embedding=list(d.embedding) if d.embedding is not None else None,
            )
            for d in docs
        ]
        return await sdk.add_docs(name, sdk_docs, MutationOptions(upsert=True))

    async def wait_for_job(self, job_id: str, *, timeout_s: float = 90.0) -> str:
        """Poll ``getJobStatus`` until the build COMPLETES or FAILS.

        Returns the final status string. Raises on FAILED / timeout."""
        import asyncio
        import time

        sdk = self._ensure()
        deadline = time.monotonic() + timeout_s
        last = "unknown"
        while time.monotonic() < deadline:
            st = await sdk.get_job_status(job_id)
            last = str(getattr(getattr(st, "status", None), "value", st.status)).upper()
            if last in ("COMPLETED", "FAILED"):
                if last == "FAILED":
                    raise RuntimeError(
                        f"Moss build job {job_id} FAILED: {getattr(st, 'error', '')}"
                    )
                return last
            await asyncio.sleep(1.5)
        raise TimeoutError(f"Moss build job {job_id} did not complete in {timeout_s}s "
                           f"(last status {last})")

    # -- index loading + query ---------------------------------------------
    async def load_index(self, name: str) -> None:
        """Pull the built index into the in-memory runtime so queries run local
        (the sub-10 ms path). Idempotent per client instance."""
        sdk = self._ensure()
        await sdk.load_index(name)
        self._loaded.add(name)

    async def search(
        self,
        name: str,
        query: str,
        *,
        top_k: int = 5,
        embedding: Optional[Sequence[float]] = None,
        alpha: float = 0.8,
    ) -> MossSearch:
        """Run a semantic search. If the index was ``load_index``-ed, this runs
        locally (~1-10 ms). When ``embedding`` is supplied (custom-embedding
        index) the runtime never needs the moss-minilm model host."""
        from moss import QueryOptions

        sdk = self._ensure()
        opts = QueryOptions(
            top_k=top_k,
            alpha=alpha,
            embedding=list(embedding) if embedding is not None else None,
        )
        sr = await sdk.query(name, query, opts)
        hits = [
            MossHit(
                id=getattr(d, "id", ""),
                text=getattr(d, "text", ""),
                score=float(getattr(d, "score", 0.0)),
                metadata=getattr(d, "metadata", None),
            )
            for d in getattr(sr, "docs", [])
        ]
        return MossSearch(
            hits=hits,
            query=getattr(sr, "query", query),
            index_name=getattr(sr, "index_name", name),
            time_taken_ms=getattr(sr, "time_taken_ms", None),
        )

    # -- reads / housekeeping ----------------------------------------------
    async def list_indexes(self) -> list[Any]:
        return await self._ensure().list_indexes()

    async def delete_index(self, name: str) -> bool:
        return await self._ensure().delete_index(name)


__all__ = ["MossClient", "MossDoc", "MossHit", "MossSearch", "builtin_embed_model"]
