"""``MossMemory`` — the live ``Memory`` adapter (execution §2.2 CONFIRM, §6).

Durable write-back of verified facts + a profile read, backed by Moss.

  - ``write(fact)`` upserts the fact into a per-user Moss memory index (a custom
    Gemini-embedding index, like the KB). The fact's ``source_node_id``,
    ``polarity`` and ``verified`` flag are carried in the doc metadata so they
    survive the round-trip (the grounding invariant must hold on read-back too).
  - ``read_profile(user_id)`` lists the user's stored facts back as a ``Profile``.

Moss has no native "list every doc filtered by user" beyond ``getDocs``; we scope
by giving each user their own index (``{prefix}-{user_id}``) so ``get_docs``
returns exactly that user's facts. Writes are batched-of-one upserts and wait for
the build, so a written fact reads back immediately (R3 acceptance).
"""

from __future__ import annotations

import hashlib
import os
import time

from clarion.contracts.ports import Memory
from clarion.contracts.state import Fact, Profile

from clarion.retrieval.ingest_gemini import GeminiEmbedder
from clarion.retrieval.moss_client import MossClient, MossDoc

_MEM_PREFIX = os.environ.get("MOSS_MEMORY_PREFIX", "clarion-mem")


def _index_for(user_id: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in user_id).lower()
    return f"{_MEM_PREFIX}-{safe}"


class MossMemory(Memory):
    """Per-user durable fact store on Moss.

    Args:
        moss:     a ``MossClient`` (defaults to one from env creds).
        embedder: a ``GeminiEmbedder`` (defaults to one from env creds).
        user_id:  the user whose memory this instance binds to. The kernel writes
                  facts without a user binding (see ``fakes.FakeMemory``); the
                  binding happens here at the adapter, defaulting to ``"default"``.
    """

    def __init__(
        self,
        *,
        moss: MossClient | None = None,
        embedder: GeminiEmbedder | None = None,
        user_id: str = "default",
    ) -> None:
        self._moss = moss or MossClient()
        self._embedder = embedder or GeminiEmbedder()
        self._user_id = user_id

    async def write(self, fact: Fact) -> None:
        """Durably upsert ``fact`` into the user's Moss memory index."""
        index = _index_for(self._user_id)
        vec = (await self._embedder.embed([fact.value]))[0]
        doc_id = (
            f"{index}::"
            + hashlib.sha1(fact.value.encode("utf-8")).hexdigest()[:12]
        )
        meta = {
            "source_node_id": fact.source_node_id or "",
            "polarity": fact.polarity,
            "verified": "true" if fact.verified else "false",
            "written_at": f"{time.time():.0f}",
        }
        doc = MossDoc(id=doc_id, text=fact.value, metadata=meta, embedding=vec)

        existing = {getattr(i, "name", None) for i in await self._moss.list_indexes()}
        if index in existing:
            res = await self._moss.add_docs(index, [doc])
        else:
            res = await self._moss.create_index(index, [doc], model_id="custom")
        job_id = getattr(res, "job_id", None)
        if job_id:
            await self._moss.wait_for_job(job_id)

    async def read_profile(self, user_id: str) -> Profile:
        """Read back the user's stored facts as a ``Profile`` (empty if none)."""
        index = _index_for(user_id)
        existing = {getattr(i, "name", None) for i in await self._moss.list_indexes()}
        if index not in existing:
            return Profile(user_id=user_id, facts=[])

        sdk = self._moss._ensure()  # get_docs is a read-only SDK call
        docs = await sdk.get_docs(index)
        facts: list[Fact] = []
        for d in docs:
            meta = getattr(d, "metadata", None) or {}
            facts.append(
                Fact(
                    value=getattr(d, "text", ""),
                    source_node_id=(meta.get("source_node_id") or None),
                    polarity=meta.get("polarity", "present"),  # type: ignore[arg-type]
                    verified=meta.get("verified") == "true",
                    retrieved_at=0.0,
                )
            )
        return Profile(user_id=user_id, facts=facts)


__all__ = ["MossMemory"]
