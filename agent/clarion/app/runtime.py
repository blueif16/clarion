"""I1 — runtime wiring for the live hero flow (execution §1, §6, §7).

Assembles the FROZEN pieces into the runnable system the hero run + the voice
worker drive:

  - the **ST1 stage graph** (``clarion.stages.graph.build_stage_graph``) as the
    top-level task graph — NOT the bare kernel. ST1 already drives CONSENT at the
    stage-graph level (re-surfacing the kernel's ``ConsentRequest`` through the
    parent ``interrupt()``) and handles the §18.7 reducer / content-keyed dedup
    for sub-loops that re-execute across an interrupt (ST1 findings, honored).
  - a ``TimedRetriever(HeroRetriever)`` — the §8 latency source wrapping a
    deterministic, grounded hero corpus (Moss deferred — execution §17 scope-shed
    order: R3/Moss → fakes).
  - the real ``PlaywrightActuator`` over the demo site.
  - a ``PanelPublisher`` that maps ``ClarionState`` → ``PanelState`` via the pure
    ``instrument.to_panel_state`` and publishes it as a LiveKit participant
    attribute (``room.local_participant.set_attributes({"panel_state": ...})``) so
    the U1 panel reflects stage/step/consent/latency/trace live (execution §6).
    The publisher degrades to a console/recording sink when no room is attached
    (the headless hero harness), so the SAME publish path is exercised either way.

This module OWNS only ``clarion/app/``; it imports the frozen contracts + the
Wave-1 adapters / stage graph / instrument read-only.

Context7 (livekit-agents 1.5.x, /livekit/python-agents verified 2026-05-31):
  ``room.local_participant.set_attributes({...})`` publishes a dict[str,str] of
  participant attributes; the panel subscribes via
  ``RoomEvent.ParticipantAttributesChanged`` on the agent participant's
  ``panel_state`` key (web/panel ClarionPanel, ?live=1 mode).
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Callable, Literal, Optional

from clarion.contracts.ports import Actuator, Memory, Reasoner, Retriever
from clarion.contracts.state import ClarionState, Fact
from clarion.instrument.baseline import COLD_RAG_BASELINE_MS
from clarion.instrument.publisher import to_panel_state
from clarion.instrument.timed import TimedRetriever

# ---------------------------------------------------------------------------
# The hero corpus retriever (Moss deferred — fakes per §17 scope-shed).
# ---------------------------------------------------------------------------

# The demo site's real, grounded values (read off web/demo-site account + pay
# pages). Each Fact carries a source_node_id so the epistemic clause lets it be
# spoken (a None source means the policy refuses to verify it — see kernel.policy).
_HERO_FACTS: dict[str, list[Fact]] = {
    # LOCATE: amount + payee + due date — all grounded with source refs.
    "amount": [
        Fact(value="Amount due: $84.32", source_node_id="acct::balance", verified=True),
        Fact(value="Payee: Northwind Utilities", source_node_id="acct::payee", verified=True),
        Fact(value="Due date: June 15, 2026", source_node_id="acct::due", verified=True),
        # A first-class NEGATIVE fact (the epistemic clause supports polarity):
        # there is no autopay already scheduled we'd duplicate.
        Fact(
            value="No autopay scheduled",
            source_node_id="acct::autopay",
            polarity="absent",
            verified=True,
        ),
    ],
    # REVIEW: cross-check the amount against the known balance (a surprise-fee
    # negative is grounded too).
    "cross-check": [
        Fact(value="Amount due: $84.32", source_node_id="acct::balance", verified=True),
        Fact(value="Payee: Northwind Utilities", source_node_id="acct::payee", verified=True),
        Fact(
            value="No convenience fee on this payment",
            source_node_id="acct::fees",
            polarity="absent",
            verified=True,
        ),
    ],
}


class HeroRetriever(Retriever):
    """Deterministic, source-referenced facts for the hero goal.

    NOTE (Gap 1): the LIVE runtime no longer grounds on this — it grounds on the
    real page via ``clarion.app.page_retriever.PageRetriever``. This class is
    RETAINED only as a deterministic test double (its ``source_node_id``s like
    ``"acct::balance"`` are synthetic and match no real node, so it must never feed
    a live run). Matches a query against ``_HERO_FACTS`` substrings; falls back to a
    single grounded echo fact so the grounding clause always has a source."""

    def __init__(self, corpus: Optional[dict[str, list[Fact]]] = None) -> None:
        self.corpus = corpus if corpus is not None else _HERO_FACTS
        self.calls: list[str] = []

    async def query(self, q: str, *, k: int = 5) -> list[Fact]:
        self.calls.append(q)
        ql = q.lower()
        for needle, facts in self.corpus.items():
            if needle.lower() in ql or any(
                kw in ql for kw in _NEEDLE_KEYWORDS.get(needle, ())
            ):
                return [f.model_copy(update={"retrieved_at": time.time()}) for f in facts[:k]]
        # Grounded fallback echo (never ungrounded — the policy would refuse it).
        return [
            Fact(
                value=f"result for: {q}",
                source_node_id=f"hero-doc::{abs(hash(q)) % 10_000}",
                verified=True,
                retrieved_at=time.time(),
            )
        ][:k]


# Map a stage goal phrase to the corpus needle (so the kernel's GROUND, which
# queries with the *stage goal*, hits the right grounded facts).
_NEEDLE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "amount": ("amount", "payee", "due", "find", "locate"),
    "cross-check": ("cross-check", "review", "before paying"),
}


# ---------------------------------------------------------------------------
# The KB (Moss) retriever selector — LIVE Moss vs OFFLINE cached replay (I2).
# ---------------------------------------------------------------------------


class CachedRetriever(Retriever):
    """OFFLINE replay of a recorded REAL Moss query result (``CLARION_DEMO_MODE``).

    Serves the grounded KB ``Fact``s captured by ``record_moss_fixture`` from the
    live ``clarion-kb`` index — no network, no Gemini embed RPC, no Moss runtime —
    so the offline hero run still shows the (recorded, real) Moss number and the
    grounded KB fact. We replay what Moss *returned*, never a fabricated answer.

    Exposes ``last_runtime_ms`` (the recorded Moss IN-MEMORY number) so the SAME
    panel wire that reads the live Moss number reads the cached one — labelled
    "[cached]" by the harness so the demo never claims a live measurement.
    """

    def __init__(self, *, path: Optional[str] = None) -> None:
        from clarion.app.kb_beat import MOSS_FIXTURE_PATH

        self._path = path or MOSS_FIXTURE_PATH
        self._rec = self._load(self._path)
        self._last_runtime_ms: Optional[float] = self._rec.get("last_runtime_ms")
        self.index: str = self._rec.get("index", "clarion-kb")
        self.calls: list[str] = []

    @staticmethod
    def _load(path: str) -> dict:
        if not os.path.exists(path):
            raise FileNotFoundError(
                f"Moss KB fixture not found at {path!r}. Record it with the live "
                f"stack reachable:  .venv/bin/python -m clarion.app.record_moss_fixture"
            )
        with open(path) as f:
            return json.load(f)

    @property
    def last_runtime_ms(self) -> Optional[float]:
        """The recorded Moss IN-MEMORY search time (the panel number, offline)."""
        return self._last_runtime_ms

    async def query(self, q: str, *, k: int = 5) -> list[Fact]:
        self.calls.append(q)
        facts = [
            Fact(
                value=d["value"],
                source_node_id=d.get("source_node_id"),
                polarity=d.get("polarity", "present"),
                verified=bool(d.get("verified", False)),
                retrieved_at=time.time(),
            )
            for d in self._rec.get("facts", [])
        ]
        return facts[:k]


async def select_kb_retriever(*, demo_mode: bool) -> Retriever:
    """Pick the KB (Moss) retriever per execution §9 / §17.

    - LIVE (default): ``TimedRetriever(MossRetriever(...))`` over the prebuilt
      ``clarion-kb`` index. The index is ingested ONCE if missing and REUSED
      thereafter (``kb_beat.ensure_kb_index`` — never re-ingest every run; ingest
      builds a cloud index that takes time). The wrapper exposes wall-clock ms; the
      inner ``MossRetriever`` exposes the in-memory ``last_runtime_ms`` for the
      panel (R-Moss guidance).
    - DEMO (``CLARION_DEMO_MODE=1``): ``CachedRetriever`` replaying the recorded
      real Moss result — OFFLINE, no network.
    """
    if demo_mode:
        return CachedRetriever()
    from clarion.app.kb_beat import ensure_kb_index
    from clarion.retrieval import MossRetriever

    index, ingested = await ensure_kb_index()
    return TimedRetriever(MossRetriever(index=index))


# ---------------------------------------------------------------------------
# The panel publisher (execution §6) — the one I1 seam the instrument left open.
# ---------------------------------------------------------------------------


class PanelPublisher:
    """Maps ``ClarionState`` → ``PanelState`` (pure ``to_panel_state``) and
    publishes it as a LiveKit participant attribute.

    The publish target is a ``room`` with ``local_participant.set_attributes`` (the
    live path the U1 panel subscribes to). When no room is attached — the headless
    hero harness — it routes the SAME JSON to an optional ``sink`` callback (and
    keeps a ``published`` log) so the publish path is exercised and provable
    identically (honest: live publish vs recorded publish, same serialization).

    Args:
        room:       a LiveKit ``Room`` (its ``local_participant.set_attributes``
                    is awaited). None for the headless harness.
        retriever:  the ``TimedRetriever`` whose ``last_query_ms`` feeds the live
                    latency meter. None suppresses the live number.
        baseline_ms: the greyed cold-RAG baseline (default ``COLD_RAG_BASELINE_MS``).
        sink:       optional callback receiving (PanelState, json_str) per publish
                    (the harness uses this to print / record the published JSON).
    """

    def __init__(
        self,
        *,
        room: Optional[Any] = None,
        retriever: Optional[TimedRetriever] = None,
        baseline_ms: Optional[float] = COLD_RAG_BASELINE_MS,
        sink: Optional[Callable[[Any, str], None]] = None,
    ) -> None:
        self._room = room
        self._retriever = retriever
        self._baseline_ms = baseline_ms
        self._sink = sink
        # A record of every publish (for the harness's PanelState evidence).
        self.published: list[dict] = []

    async def publish(
        self, state: ClarionState, *, retrieval_ms_override: Optional[float] = None
    ) -> str:
        """Build the PanelState from ``state`` and publish it. Returns the JSON
        string that was published (the exact payload the panel receives).

        ``retrieval_ms_override`` lets the KB-retrieval beat publish the Moss
        IN-MEMORY ``last_runtime_ms`` (R-Moss guidance: the sub-ms in-memory number,
        NOT the wall-clock that includes the Gemini embed RPC). Other beats fall
        back to the wrapped retriever's wall-clock ``last_query_ms``.
        """
        if retrieval_ms_override is not None:
            retrieval_ms = retrieval_ms_override
        else:
            retrieval_ms = (
                self._retriever.last_query_ms if self._retriever is not None else None
            )
        panel = to_panel_state(
            state, retrieval_ms=retrieval_ms, baseline_ms=self._baseline_ms
        )
        payload = panel.model_dump_json()
        self.published.append(json.loads(payload))

        if self._room is not None:
            # LIVE: publish as a participant attribute (the panel's ?live=1 wire).
            await self._room.local_participant.set_attributes({"panel_state": payload})

        if self._sink is not None:
            self._sink(panel, payload)

        return payload


# ---------------------------------------------------------------------------
# Runtime assembly.
# ---------------------------------------------------------------------------


class HeroRuntime:
    """The wired live system: stage graph + timed retriever + actuator + panel
    publisher + policy/mode. Construct via ``HeroRuntime.create``.

    Two retrievers, because there are TWO kinds of grounded fact (kept distinct):
      - ``retriever`` (PAGE facts): the stage-graph GROUND source for the values
        read off the page (amount/payee/due) — a ``PageRetriever`` over the
        actuator that reads the REAL live AX tree, each fact sourced to a live
        node (foundation §1 / Gap 1; replaced the ``HeroRetriever`` fixture).
      - ``kb_retriever`` (KB facts): the LIVE Moss retriever over the ingested
        Northwind policy (late-fee / autopay terms) — the §8 latency-meter +
        negative-verification beat. ``CLARION_DEMO_MODE=1`` swaps it for a
        ``CachedRetriever`` (offline replay of a recorded real Moss result).
    """

    def __init__(
        self,
        *,
        actuator: Actuator,
        retriever: TimedRetriever,
        publisher: PanelPublisher,
        mode: Literal["normal", "fast"],
        reasoner: Reasoner,
        kb_retriever: Optional[Retriever] = None,
        kb_live: bool = True,
        memory: Optional[Memory] = None,
        user_id: str = "default",
    ) -> None:
        self.actuator = actuator
        self.retriever = retriever
        self.publisher = publisher
        self.mode = mode
        # The de-hardcoding boundary: the LLM that reasons the plan + next step
        # (MinimaxReasoner / MiniMax-M3 live; FakeReasoner in tests). Injected into
        # the executor.
        self.reasoner = reasoner
        # The Moss-backed KB retriever (live) or the offline cached replay (demo).
        self.kb_retriever = kb_retriever
        self.kb_live = kb_live
        # The Moss-backed user-memory store (the knowledge layer): the run's
        # episode write-back + the planner's recall. None when memory is off.
        self.memory = memory
        self.user_id = user_id

    @classmethod
    async def create(
        cls,
        demo_site_url: str,
        *,
        mode: Literal["normal", "fast"] = "fast",
        room: Optional[Any] = None,
        headless: bool = True,
        panel_sink: Optional[Callable[[Any, str], None]] = None,
        retriever: Optional[Retriever] = None,
        kb_retriever: Optional[Retriever] = None,
        actuator: Optional[Actuator] = None,
        reasoner: Optional[Reasoner] = None,
        memory: Optional[Memory] = None,
        user_id: str = "default",
    ) -> "HeroRuntime":
        """Build the runtime over the live demo site.

        - ``TimedRetriever(PageRetriever(actuator))`` — the page-fact stage GROUND
          source: reads the REAL live page off the actuator, every fact sourced to
          a live AX node (Gap 1; an explicit ``retriever`` still overrides).
        - ``kb_retriever`` — the KB (Moss) retriever selected by
          ``select_kb_retriever``: LIVE ``TimedRetriever(MossRetriever)`` over the
          prebuilt ``clarion-kb`` index (ingested ONCE if missing, reused after),
          OR — in ``CLARION_DEMO_MODE=1`` — a ``CachedRetriever`` replaying the
          recorded real Moss result OFFLINE (no network).
        - the Actuator: ``PlaywrightActuator`` over ``demo_site_url`` (the live,
          autonomous default), OR — when ``CLARION_DEMO_MODE=1`` — a
          ``CachedActuator`` that REPLAYS the recorded fixture (no browser, no
          network) so the FULL hero run is judge-proof even with the site/network
          down (execution §9). Only PERCEPTION is cached; the K1 kernel + ST1
          stage graph + consent gate + policy still execute for real. An explicit
          ``actuator`` (e.g. an ``ExtensionActuator`` over a started
          ``WebSocketCdpRelay`` — the chrome.debugger / extension path) is injected
          verbatim, so the SAME stage/perceive path drives the user's real tab.
        - ``PanelPublisher`` (live if ``room`` given, else recording sink).
        """
        from clarion.app.demo_mode import CachedActuator, demo_mode_enabled
        from clarion.app.page_retriever import PageRetriever

        demo = demo_mode_enabled()
        # Resolve the actuator FIRST — the page-fact retriever grounds on it.
        if actuator is not None:
            # Injected transport (the extension path) — used as-is; no browser spawn.
            pass
        elif demo:
            actuator = await CachedActuator.create(demo_site_url, headless=headless)
        else:
            from clarion.actuator.actuator import PlaywrightActuator

            actuator = await PlaywrightActuator.create(demo_site_url, headless=headless)
        # The PAGE-fact retriever for the kernel's GROUND: read the REAL page off
        # the actuator (every fact sourced to a live AX node), NOT the HeroRetriever
        # fixture (foundation §1 / Gap 1). An explicit ``retriever`` still overrides
        # (the tests inject their own); absent one we ground on the live page.
        timed = TimedRetriever(retriever or PageRetriever(actuator))
        # The KB (Moss) retriever — live by default, cached + offline in demo mode.
        kb = kb_retriever if kb_retriever is not None else await select_kb_retriever(
            demo_mode=demo
        )
        # The de-hardcoding boundary: the real MinimaxReasoner (MiniMax-M3, the LLM
        # decider) unless one is injected (tests inject a FakeReasoner). Lazy client
        # — no I/O at construct (load_dotenv resolves agent/.env keys on first call).
        if reasoner is None:
            from clarion.adapters.minimax_reasoner import MinimaxReasoner

            reasoner = MinimaxReasoner()
        # The user-memory store (the knowledge layer). Opt-in (live) via
        # CLARION_MEMORY=1 — default None keeps memory off and the no-network gate
        # untouched. An explicit ``memory`` (tests inject FakeMemory) overrides.
        if memory is None and os.environ.get("CLARION_MEMORY") == "1":
            from clarion.retrieval.memory_moss import MossMemory

            memory = MossMemory(user_id=user_id)
        publisher = PanelPublisher(room=room, retriever=timed, sink=panel_sink)
        return cls(
            actuator=actuator,
            retriever=timed,
            publisher=publisher,
            mode=mode,
            reasoner=reasoner,
            kb_retriever=kb,
            kb_live=not demo,
            memory=memory,
            user_id=user_id,
        )

    async def kb_beat(self, *, page_late_fee_present: bool = False):
        """Run the KB-retrieval beat (§6/§8) over ``kb_retriever``: the grounded
        KB facts + their Moss ``source_node_id`` + the in-memory ``last_runtime_ms``
        + the negative-verification fact. LIVE queries Moss; demo replays the cache.
        """
        from clarion.app.kb_beat import MossKBBeat

        if self.kb_retriever is None:
            raise RuntimeError("no kb_retriever configured on this runtime")
        if self.kb_live:
            return await MossKBBeat.from_live(
                self.kb_retriever, page_late_fee_present=page_late_fee_present
            )
        # Demo mode: the CachedRetriever IS the recorded result — replay from cache.
        return MossKBBeat.from_cache(page_late_fee_present=page_late_fee_present)

    def build_stage_graph(self):
        """Compile the generic executor graph wired with the injected Reasoner +
        the timed retriever + the live actuator + this runtime's mode. The graph is
        the top-level task graph (drive CONSENT at the executor level, not the bare
        kernel). The Reasoner is the de-hardcoding boundary — the plan + every step
        are LLM-derived, ZERO baked topology."""
        from clarion.stages.graph import build_stage_graph

        # Knowledge-layer #4(a): consult the per-site STRUCTURE index at PLAN time so
        # the Reasoner knows which page hosts the goal's flow. Opt-in (live only) via
        # CLARION_SITE_KNOWLEDGE=1 — default OFF keeps the page-only planner and the
        # no-network test gate untouched. Fail-open inside SiteKnowledge.
        site_context = None
        if os.environ.get("CLARION_SITE_KNOWLEDGE") == "1":
            from clarion.app.site_indexer import SiteKnowledge

            site_context = SiteKnowledge().context_facts

        # Knowledge-layer #4(c): the end-of-flow "remember?" offer's nominator —
        # wraps app.remember's secret-suppressing nomination so stages/ stays app-free
        # (mirrors site_context). Active ONLY when memory is on (CLARION_MEMORY=1 builds
        # self.memory); default OFF → the stage graph never reaches the remember node.
        remember_nominate = None
        if self.memory is not None and os.environ.get("CLARION_MEMORY") == "1":
            from clarion.app.remember import nominate_remember_candidates

            def _nominate(filled, page):
                return [
                    (c.key, c.value) for c in nominate_remember_candidates(filled, page)
                ]

            remember_nominate = _nominate

        # Knowledge-layer AUTO-INDEX: when opted in (CLARION_AUTO_INDEX=1, default
        # OFF), hand the planner a fire-and-forget hook that schedules a background,
        # read-only PUBLIC structure crawl of the current host (cookie-less → can't
        # touch the user's private pages; throttled per host; fail-open). Default OFF
        # keeps the no-network gate + the event-day live worker untouched.
        on_orient = None
        if os.environ.get("CLARION_AUTO_INDEX") == "1":
            from clarion.app.auto_index import schedule_auto_index

            on_orient = schedule_auto_index

        return build_stage_graph(
            self.reasoner,
            self.retriever,
            self.actuator,
            mode=self.mode,
            site_context=site_context,
            on_orient=on_orient,
            memory=self.memory,
            user_id=self.user_id,
            remember_nominate=remember_nominate,
        )

    async def close(self) -> None:
        await self.actuator.close()


__all__ = [
    "HeroRetriever",
    "CachedRetriever",
    "select_kb_retriever",
    "PanelPublisher",
    "HeroRuntime",
    "COLD_RAG_BASELINE_MS",
]
