"""``MossMemory`` — the live ``Memory`` adapter (execution §2.2 CONFIRM, §6; the
user-memory knowledge layer, backlog #4).

Durable, Moss-backed user memory. ONE per-user index (``{prefix}-{user_id}``)
holds three ``kind``-discriminated doc types — Moss has no metadata filter beyond
``get_docs``, so user scope = the index name and kind partitioning is client-side:

  - ``kind="fact"``       — the existing CONFIRM write-back. ``write(fact)`` upserts
    the verified fact; its ``source_node_id``/``polarity``/``verified`` ride the doc
    metadata so the grounding invariant holds on read-back. Synchronous (waits for
    the build) so a written fact reads back immediately (R3).
  - ``kind="preference"`` — a remembered standing trait, captured ONLY via the
    consent-gated "remember?" offer (no memory without a yes). ``source_node_id``
    is ALWAYS empty: a preference is never page-grounded. Keyed by ``pref_key`` so a
    new value UPSERTS. Fire-and-forget.
  - ``kind="episode"``    — a completed-workflow record (the reasoned plan + consent
    decisions + timings). Keyed by ``(goal_norm, url_host)`` so a re-run upserts the
    latest good path. Stores the plan SHAPE, NEVER a grounded value. Fire-and-forget.

``recall`` returns a ``Recall`` (plan hint + preferences + consent reminder) —
NEVER a ``Fact`` — so a remembered value cannot enter the kernel as a grounded
fact; it warm-starts the next plan and is re-grounded live before anything is
spoken (the invariant firewall — see ``contracts.state.Recall``).
"""

from __future__ import annotations

import hashlib
import json
import os
import time

from clarion.contracts.ports import Memory
from clarion.contracts.state import (
    ConsentRecord,
    Fact,
    Profile,
    Recall,
    Subgoal,
    WorkflowEpisode,
)

from clarion.retrieval.ingest_gemini import GeminiEmbedder
from clarion.retrieval.moss_client import MossClient, MossDoc, builtin_embed_model

_MEM_PREFIX = os.environ.get("MOSS_MEMORY_PREFIX", "clarion-mem")

_VALID_OUTCOMES = ("completed", "declined", "error")


def _index_for(user_id: str) -> str:
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in user_id).lower()
    return f"{_MEM_PREFIX}-{safe}"


def _normalize_goal(goal: str) -> str:
    """Collapse whitespace + lowercase so re-running the same goal hashes to the
    same episode doc id (the upsert key)."""
    return " ".join((goal or "").lower().split())


def _episode_text(ep: WorkflowEpisode) -> str:
    """The embeddable text for an episode: goal + the plan in words, so a NEW goal
    semantically recalls the nearest past path. NEVER a grounded value."""
    descs = " · ".join(s.description for s in ep.subgoals if s.description)
    return f"{ep.goal} | {descs} {ep.plan_utterance}".strip()


def _int(x: object) -> int:
    try:
        return int(x)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _flt(x: object) -> float:
    try:
        return float(x)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def _episode_from_meta(meta: dict) -> WorkflowEpisode:
    """Rebuild a ``WorkflowEpisode`` from its Moss doc metadata (the JSON-encoded
    subgoals/consent decoded back into the frozen value objects). Never produces a
    ``Fact`` — an episode is structurally incapable of carrying a citable value."""
    subs: list[Subgoal] = []
    try:
        for s in json.loads(meta.get("subgoals_json") or "[]"):
            subs.append(
                Subgoal(
                    description=s.get("description", ""),
                    done_check=s.get("done_check", ""),
                )
            )
    except (ValueError, TypeError, AttributeError):
        pass
    consent: list[ConsentRecord] = []
    try:
        for c in json.loads(meta.get("consent_json") or "[]"):
            consent.append(
                ConsentRecord(
                    proposal_id=c.get("proposal_id", ""),
                    utterance=c.get("utterance", ""),
                    irreversible=bool(c.get("irreversible", False)),
                    decision=c.get("decision", ""),
                )
            )
    except (ValueError, TypeError, AttributeError):
        pass
    outcome = meta.get("outcome", "completed")
    if outcome not in _VALID_OUTCOMES:
        outcome = "completed"
    return WorkflowEpisode(
        goal=meta.get("goal", ""),
        url_host=meta.get("url_host", ""),
        subgoals=subs,
        plan_utterance=meta.get("plan_utterance", ""),
        outcome=outcome,  # type: ignore[arg-type]
        consent=consent,
        hard_stops=_int(meta.get("hard_stops")),
        approvals=_int(meta.get("approvals")),
        decide_ms_mean=_flt(meta.get("decide_ms_mean")),
        perceive_ms_mean=_flt(meta.get("perceive_ms_mean")),
        completed_at=_flt(meta.get("completed_at")),
    )


class MossMemory(Memory):
    """Per-user durable user-memory store on Moss (facts + preferences + episodes).

    Args:
        moss:     a ``MossClient`` (defaults to one from env creds).
        embedder: a ``GeminiEmbedder`` (defaults to one from env creds, unless the
                  built-in Moss embed path is selected — then ``None``).
        user_id:  the user this instance binds to for ``write(fact)``. The new
                  methods take ``user_id`` explicitly (matching the port). Single-
                  user for the event → ``"default"``.
    """

    def __init__(
        self,
        *,
        moss: MossClient | None = None,
        embedder: GeminiEmbedder | None = None,
        user_id: str = "default",
    ) -> None:
        self._moss = moss or MossClient()
        # Built-in Moss model → no external embedder; else Gemini custom vectors.
        self._builtin = builtin_embed_model()
        self._embedder = embedder or (None if self._builtin else GeminiEmbedder())
        self._user_id = user_id

    # -- internal helpers ---------------------------------------------------
    async def _embed(self, text: str):
        """The doc/query vector for the custom (Gemini) path, or ``None`` on the
        built-in path (Moss embeds locally)."""
        if self._embedder is None:
            return None
        return (await self._embedder.embed([text]))[0]

    async def _upsert(self, index: str, doc: MossDoc, *, wait: bool) -> None:
        """Create the index if absent, else ``add_docs`` (upsert). ``wait`` blocks
        on the build for read-back immediacy (facts, R3); ``wait=False`` is
        fire-and-forget (preferences/episodes — nothing re-reads them this session
        and the write sits at run-finish, off the <800ms turn budget)."""
        existing = {getattr(i, "name", None) for i in await self._moss.list_indexes()}
        if index in existing:
            res = await self._moss.add_docs(index, [doc])
        else:
            res = await self._moss.create_index(
                index, [doc], model_id=self._builtin or "custom"
            )
        if wait:
            job_id = getattr(res, "job_id", None)
            if job_id:
                await self._moss.wait_for_job(job_id)

    # -- facts (existing CONFIRM write-back, now kind-stamped) --------------
    async def write(self, fact: Fact) -> None:
        """Durably upsert ``fact`` into the user's Moss memory index (synchronous —
        reads back immediately)."""
        index = _index_for(self._user_id)
        vec = await self._embed(fact.value)
        doc_id = f"{index}::" + hashlib.sha1(fact.value.encode("utf-8")).hexdigest()[:12]
        meta = {
            "kind": "fact",
            "source_node_id": fact.source_node_id or "",
            "polarity": fact.polarity,
            "verified": "true" if fact.verified else "false",
            "written_at": f"{time.time():.0f}",
        }
        await self._upsert(
            index, MossDoc(id=doc_id, text=fact.value, metadata=meta, embedding=vec), wait=True
        )

    # -- preferences (consent-gated "remember?" capture) -------------------
    async def write_preference(
        self, user_id: str, key: str, value: str, *, origin: str = "stated"
    ) -> None:
        """Remember a standing trait. Keyed by ``key`` so a new value upserts.
        ``source_node_id`` is ALWAYS empty — a preference is never page-grounded.
        Fire-and-forget."""
        index = _index_for(user_id)
        text = f"{key}: {value}"
        vec = await self._embed(text)
        doc_id = f"{index}::pref::" + hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]
        meta = {
            "kind": "preference",
            "pref_key": key,
            "pref_value": value,
            "source_node_id": "",
            "origin": origin,
            "written_at": f"{time.time():.0f}",
        }
        await self._upsert(
            index, MossDoc(id=doc_id, text=text, metadata=meta, embedding=vec), wait=False
        )

    # -- episodes (completed-workflow record) ------------------------------
    async def write_episode(self, user_id: str, episode: WorkflowEpisode) -> None:
        """Persist a completed-workflow record, upserted by ``(goal_norm, url_host)``
        so a re-run keeps the latest good path. Stores the plan SHAPE + consent
        decisions + timings; NEVER a grounded value. Fire-and-forget."""
        index = _index_for(user_id)
        goal_norm = _normalize_goal(episode.goal)
        text = _episode_text(episode)
        vec = await self._embed(text)
        doc_id = (
            f"{index}::ep::"
            + hashlib.sha1(f"{goal_norm}|{episode.url_host}".encode("utf-8")).hexdigest()[:12]
        )
        meta = {
            "kind": "episode",
            "goal": episode.goal,
            "goal_norm": goal_norm,
            "url_host": episode.url_host,
            "outcome": episode.outcome,
            "subgoals_json": json.dumps(
                [{"description": s.description, "done_check": s.done_check} for s in episode.subgoals]
            ),
            "plan_utterance": episode.plan_utterance,
            "consent_json": json.dumps([c.model_dump() for c in episode.consent]),
            "hard_stops": str(episode.hard_stops),
            "approvals": str(episode.approvals),
            "decide_ms_mean": f"{episode.decide_ms_mean:.0f}",
            "perceive_ms_mean": f"{episode.perceive_ms_mean:.0f}",
            "completed_at": f"{episode.completed_at:.0f}",
            "n_subgoals": str(len(episode.subgoals)),
            "schema": "v1",
        }
        await self._upsert(
            index, MossDoc(id=doc_id, text=text, metadata=meta, embedding=vec), wait=False
        )

    # -- recall (warm-start the next run) ----------------------------------
    async def recall(
        self, user_id: str, goal: str, url_host: str, *, k: int = 3
    ) -> Recall:
        """Return a ``Recall`` for the next run: the nearest past EPISODE by
        goal-embedding similarity (semantic, site-agnostic) + ALL the user's
        preferences + the matched episode's consent reminder. NEVER reconstructs a
        ``Fact`` and NEVER stamps a ``source_node_id`` — a remembered value re-enters
        only as an advisory hint to re-ground live."""
        index = _index_for(user_id)
        existing = {getattr(i, "name", None) for i in await self._moss.list_indexes()}
        if index not in existing:
            return Recall()

        # (1) semantic search on the goal → the nearest episode-kind doc.
        plan_hint: WorkflowEpisode | None = None
        best_sim = 0.0
        try:
            await self._moss.load_index(index)
            vec = await self._embed(goal)
            res = await self._moss.search(
                index, goal, top_k=max(k, 5), embedding=vec, alpha=0.6
            )
            for hit in res.hits:
                meta = hit.metadata or {}
                if meta.get("kind") == "episode":
                    plan_hint = _episode_from_meta(meta)
                    best_sim = hit.score
                    break
        except Exception:  # noqa: BLE001 — recall is advisory; never break planning.
            plan_hint = None

        # (2) ALL preferences (a recall surfaces every pref, not just ranked hits).
        preferences: dict[str, str] = {}
        try:
            sdk = self._moss._ensure()
            for d in await sdk.get_docs(index):
                meta = getattr(d, "metadata", None) or {}
                if meta.get("kind") == "preference":
                    pk = meta.get("pref_key")
                    if pk:
                        preferences[pk] = meta.get("pref_value", "")
        except Exception:  # noqa: BLE001
            pass

        return Recall(
            plan_hint=plan_hint,
            preferences=preferences,
            consent_recall=list(plan_hint.consent) if plan_hint else [],
            similarity=best_sim,
        )

    # -- profile read-back -------------------------------------------------
    async def read_profile(self, user_id: str) -> Profile:
        """Read the user's stored memory back as a ``Profile`` (facts + preferences
        + episodes; empty if none). Facts rebuild as ``Fact`` carrying their stored
        ``source_node_id``; preferences/episodes do NOT — they are not speakable
        facts."""
        index = _index_for(user_id)
        existing = {getattr(i, "name", None) for i in await self._moss.list_indexes()}
        if index not in existing:
            return Profile(user_id=user_id, facts=[])

        sdk = self._moss._ensure()  # get_docs is a read-only SDK call
        docs = await sdk.get_docs(index)
        facts: list[Fact] = []
        preferences: dict[str, str] = {}
        episodes: list[WorkflowEpisode] = []
        for d in docs:
            meta = getattr(d, "metadata", None) or {}
            kind = meta.get("kind", "fact")
            if kind == "preference":
                pk = meta.get("pref_key")
                if pk:
                    preferences[pk] = meta.get("pref_value", "")
            elif kind == "episode":
                episodes.append(_episode_from_meta(meta))
            else:  # "fact" — default keeps back-compat with pre-kind docs.
                facts.append(
                    Fact(
                        value=getattr(d, "text", ""),
                        source_node_id=(meta.get("source_node_id") or None),
                        polarity=meta.get("polarity", "present"),  # type: ignore[arg-type]
                        verified=meta.get("verified") == "true",
                        retrieved_at=0.0,
                    )
                )
        return Profile(
            user_id=user_id, facts=facts, preferences=preferences, episodes=episodes
        )


__all__ = ["MossMemory"]
