"""``MossMemory`` — the live ``Memory`` adapter (execution §2.2 CONFIRM, §6; the
user-memory knowledge layer, backlog #4 b+c).

Durable, Moss-backed user memory on the CATEGORY-INDEX + metadata-filter pattern
(``docs/research/moss-index-design.md``) — NOT one index per user. Two shared
category indexes, every doc tagged ``{user_id, kind}`` and scoped by a Moss
``QueryOptions.filter`` (``user_id $eq <uid>``), matching how site-structure
already partitions ``clarion-site-structure`` by ``{site}``:

  - ``clarion-profile``     — the user's FACTS (the existing CONFIRM write-back,
    ``kind="fact"``) + PREFERENCES (``kind="preference"``, captured only via the
    consent-gated "remember?" offer — *no memory without a yes*; ``source_node_id``
    always empty, a preference is never page-grounded).
  - ``clarion-task-paths``  — completed-workflow EPISODES (``kind="episode"``): the
    reasoned plan + consent decisions + timings, keyed by ``(user_id, goal, site)``
    so a re-run upserts the latest good path. Stores the plan SHAPE, NEVER a value.

``recall`` returns a ``Recall`` (plan hint + preferences + consent reminder) — NEVER
a ``Fact`` — so a remembered value cannot enter the kernel as a grounded fact; it
warm-starts the next plan and is re-grounded live before anything is spoken (the
invariant firewall — see ``contracts.state.Recall``). Facts wait for the build
(R3 read-back); preferences/episodes are fire-and-forget (off the turn budget).

Index names are overridable (``MOSS_PROFILE_INDEX`` / ``MOSS_TASKPATHS_INDEX`` or
the ctor args) so a live test can use a disposable index it is safe to delete.
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

_PROFILE_INDEX = os.environ.get("MOSS_PROFILE_INDEX", "clarion-profile")
_TASKPATHS_INDEX = os.environ.get("MOSS_TASKPATHS_INDEX", "clarion-task-paths")

_VALID_OUTCOMES = ("completed", "declined", "error")


def _user_filter(user_id: str) -> dict:
    """The Moss metadata predicate that scopes a shared category index to ONE user
    (the multi-tenant partition key — `docs/research/moss-index-design.md`)."""
    return {"field": "user_id", "condition": {"$eq": user_id}}


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
        url_host=meta.get("site", "") or meta.get("url_host", ""),
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
    """Per-user durable user-memory store on Moss — TWO shared category indexes
    (profile + task-paths) partitioned by ``user_id`` metadata.

    Args:
        moss:            a ``MossClient`` (defaults to one from env creds).
        embedder:        a ``GeminiEmbedder`` (defaults to env; ``None`` on the
                         built-in Moss embed path).
        user_id:         the user this instance binds to for ``write(fact)``; the
                         other methods take ``user_id`` explicitly (matching the
                         port). Single-user for the event → ``"default"``.
        profile_index:   override the ``clarion-profile`` index name (a live test
                         points this at a disposable index it can delete).
        taskpaths_index: override the ``clarion-task-paths`` index name.
    """

    def __init__(
        self,
        *,
        moss: MossClient | None = None,
        embedder: GeminiEmbedder | None = None,
        user_id: str = "default",
        profile_index: str | None = None,
        taskpaths_index: str | None = None,
    ) -> None:
        self._moss = moss or MossClient()
        # Built-in Moss model → no external embedder; else Gemini custom vectors.
        self._builtin = builtin_embed_model()
        self._embedder = embedder or (None if self._builtin else GeminiEmbedder())
        self._user_id = user_id
        self._profile_index = profile_index or _PROFILE_INDEX
        self._taskpaths_index = taskpaths_index or _TASKPATHS_INDEX

    # -- internal helpers ---------------------------------------------------
    async def _embed(self, text: str):
        """The doc/query vector for the custom (Gemini) path, or ``None`` on the
        built-in path (Moss embeds locally)."""
        if self._embedder is None:
            return None
        return (await self._embedder.embed([text]))[0]

    async def _index_names(self) -> set:
        return {getattr(i, "name", None) for i in await self._moss.list_indexes()}

    async def _upsert(self, index: str, doc: MossDoc, *, wait: bool) -> None:
        """Create the category index if absent, else ``add_docs`` (upsert). ``wait``
        blocks on the build for read-back immediacy (facts, R3); ``wait=False`` is
        fire-and-forget (preferences/episodes — off the <800ms turn budget)."""
        if index in await self._index_names():
            res = await self._moss.add_docs(index, [doc])
        else:
            res = await self._moss.create_index(
                index, [doc], model_id=self._builtin or "custom"
            )
        if wait:
            job_id = getattr(res, "job_id", None)
            if job_id:
                await self._moss.wait_for_job(job_id)

    # -- facts (existing CONFIRM write-back → the profile index) ------------
    async def write(self, fact: Fact) -> None:
        """Durably upsert ``fact`` into the user's slice of ``clarion-profile``
        (synchronous — reads back immediately)."""
        idx = self._profile_index
        vec = await self._embed(fact.value)
        doc_id = (
            f"{idx}::fact::"
            + hashlib.sha1(f"{self._user_id}\x00{fact.value}".encode("utf-8")).hexdigest()[:12]
        )
        meta = {
            "kind": "fact",
            "user_id": self._user_id,
            "source_node_id": fact.source_node_id or "",
            "polarity": fact.polarity,
            "verified": "true" if fact.verified else "false",
            "written_at": f"{time.time():.0f}",
        }
        await self._upsert(
            idx, MossDoc(id=doc_id, text=fact.value, metadata=meta, embedding=vec), wait=True
        )

    # -- preferences (consent-gated "remember?" capture → profile index) ---
    async def write_preference(
        self, user_id: str, key: str, value: str, *, origin: str = "stated"
    ) -> None:
        """Remember a standing trait in ``clarion-profile``. Keyed by ``(user, key)``
        so a new value upserts. ``source_node_id`` is ALWAYS empty — a preference is
        never page-grounded. Fire-and-forget."""
        idx = self._profile_index
        text = f"{key}: {value}"
        vec = await self._embed(text)
        doc_id = (
            f"{idx}::pref::"
            + hashlib.sha1(f"{user_id}\x00{key}".encode("utf-8")).hexdigest()[:12]
        )
        meta = {
            "kind": "preference",
            "user_id": user_id,
            "pref_key": key,
            "pref_value": value,
            "source_node_id": "",
            "origin": origin,
            "written_at": f"{time.time():.0f}",
        }
        await self._upsert(
            idx, MossDoc(id=doc_id, text=text, metadata=meta, embedding=vec), wait=False
        )

    # -- episodes (completed-workflow record → task-paths index) -----------
    async def write_episode(self, user_id: str, episode: WorkflowEpisode) -> None:
        """Persist a completed-workflow record in ``clarion-task-paths``, upserted by
        ``(user, goal_norm, site)`` so a re-run keeps the latest good path. Stores
        the plan SHAPE + consent + timings; NEVER a grounded value. Fire-and-forget."""
        idx = self._taskpaths_index
        goal_norm = _normalize_goal(episode.goal)
        text = _episode_text(episode)
        vec = await self._embed(text)
        doc_id = (
            f"{idx}::ep::"
            + hashlib.sha1(
                f"{user_id}\x00{goal_norm}\x00{episode.url_host}".encode("utf-8")
            ).hexdigest()[:12]
        )
        meta = {
            "kind": "episode",
            "user_id": user_id,
            "site": episode.url_host,
            "goal": episode.goal,
            "goal_norm": goal_norm,
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
            idx, MossDoc(id=doc_id, text=text, metadata=meta, embedding=vec), wait=False
        )

    # -- recall (warm-start the next run) ----------------------------------
    async def recall(
        self, user_id: str, goal: str, url_host: str, *, k: int = 3
    ) -> Recall:
        """Return a ``Recall`` for the next run: the nearest past EPISODE by
        goal-embedding similarity (semantic, scoped to this user via a metadata
        filter) + ALL the user's preferences + the matched episode's consent
        reminder. NEVER reconstructs a ``Fact`` and NEVER stamps a
        ``source_node_id`` — a remembered value re-enters only as a hint."""
        names = await self._index_names()

        # (1) semantic search on the goal → the nearest episode-kind doc, scoped to
        #     this user by the Moss metadata filter (filter works on a loaded index).
        plan_hint: WorkflowEpisode | None = None
        best_sim = 0.0
        if self._taskpaths_index in names:
            try:
                await self._moss.load_index(self._taskpaths_index)
                vec = await self._embed(goal)
                res = await self._moss.search(
                    self._taskpaths_index,
                    goal,
                    top_k=max(k, 5),
                    embedding=vec,
                    alpha=0.6,
                    filter=_user_filter(user_id),
                )
                for hit in res.hits:
                    meta = hit.metadata or {}
                    if meta.get("kind") == "episode":
                        plan_hint = _episode_from_meta(meta)
                        best_sim = hit.score
                        break
            except Exception:  # noqa: BLE001 — recall is advisory; never break planning.
                plan_hint = None

        # (2) ALL of the user's preferences (get_docs returns the whole shared index;
        #     scope client-side by user_id — the index is small + in-memory).
        preferences: dict[str, str] = {}
        if self._profile_index in names:
            try:
                sdk = self._moss._ensure()
                for d in await sdk.get_docs(self._profile_index):
                    meta = getattr(d, "metadata", None) or {}
                    if meta.get("user_id") == user_id and meta.get("kind") == "preference":
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
        from ``clarion-profile`` + episodes from ``clarion-task-paths``, scoped to
        ``user_id``). Facts rebuild as ``Fact`` carrying their stored
        ``source_node_id``; preferences/episodes do NOT — they are not speakable."""
        names = await self._index_names()
        sdk = self._moss._ensure()
        facts: list[Fact] = []
        preferences: dict[str, str] = {}
        episodes: list[WorkflowEpisode] = []

        if self._profile_index in names:
            for d in await sdk.get_docs(self._profile_index):
                meta = getattr(d, "metadata", None) or {}
                if meta.get("user_id") != user_id:
                    continue
                if meta.get("kind") == "preference":
                    pk = meta.get("pref_key")
                    if pk:
                        preferences[pk] = meta.get("pref_value", "")
                else:  # "fact"
                    facts.append(
                        Fact(
                            value=getattr(d, "text", ""),
                            source_node_id=(meta.get("source_node_id") or None),
                            polarity=meta.get("polarity", "present"),  # type: ignore[arg-type]
                            verified=meta.get("verified") == "true",
                            retrieved_at=0.0,
                        )
                    )

        if self._taskpaths_index in names:
            for d in await sdk.get_docs(self._taskpaths_index):
                meta = getattr(d, "metadata", None) or {}
                if meta.get("user_id") == user_id and meta.get("kind") == "episode":
                    episodes.append(_episode_from_meta(meta))

        return Profile(
            user_id=user_id, facts=facts, preferences=preferences, episodes=episodes
        )


__all__ = ["MossMemory"]
