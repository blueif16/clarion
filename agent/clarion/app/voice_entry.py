"""I1 — the LiveKit worker entrypoint (execution §1, §5).

The production voice plane for the hero flow. Wires the V1 transport
(`LiveKitVoiceTransport`: live Deepgram STT + MiniMax-M3 LLM + MiniMax Speech
2.6-turbo TTS + Silero VAD + MultilingualModel turn detection) over an
`AgentSession` (MiniMax wired through the LiveKit minimax plugin), and attaches
the `advance_task` `@function_tool` that drives the ST1 **stage graph**
NON-BLOCKING (the proven S1 seam, execution §5):

  - `advance_task(user_intent)` launches a stage-graph step with
    `asyncio.ensure_future`, then `await speech_handle.wait_if_not_interrupted([task])`.
    On barge-in `speech_handle.interrupted` is True → `task.cancel()` → fill NOTHING.
    Otherwise the surfaced `ConsentRequest.utterance` is returned for the agent to
    speak as a readback; the graph keeps running in the background.
  - `confirm_consent(approved)` resumes the parked stage graph with
    `Command(resume=ConsentDecision(...))`, wrapped in `disallow_interruptions()` so
    a stray "um" can't fracture the act (execution §5).
  - The IRREVERSIBLE PAY in fast mode HARD-STOPS at the consent gate (the stage
    graph re-surfaces the kernel's `ConsentRequest` through the parent interrupt —
    the ST1 finding). The agent never presses PAY without an explicit "yes".
  - After every graph step a `PanelState` is published via
    `room.local_participant.set_attributes({"panel_state": ...})` so the U1 panel
    (?live=1) reflects stage/step/consent/latency/trace live (execution §6).

The advance/resume seam logic is `clarion.adapters.voice_livekit.advance_non_blocking`
(shared with the GATE harness — ONE implementation). The kernel-facing TTS is the
streaming `MinimaxSynthesizer` (MiniMax Speech 2.6-turbo over `/v1/t2a_v2`); the
session's audio output uses the LiveKit `minimax.TTS` plugin.

LIVE vs SIMULATED (honest, execution §18.6 / S1): construction + wiring are REAL,
and the STT/LLM/TTS are now LIVE on the AI-Studio GOOGLE_API_KEY (verified: live
LLM response + live TTS audio bytes through the reconciled adapter; see the report).
The full SPOKEN mic round-trip cannot run in this headless env (no mic / no LiveKit
room), so it is driven via LiveKit `console` mode or the `hero_harness` (which
exercises the real stage-graph consent seam programmatically). State which is which.

Run:  .venv/bin/python -m clarion.app.voice_entry console   (LiveKit text/voice console)
      .venv/bin/python -m clarion.app.voice_entry dev       (connect to a LiveKit room)
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Optional

from langgraph.types import Command

from clarion.actuator.pipeline import readout_from_selector_map
from clarion.adapters.voice_livekit import advance_non_blocking
from clarion.contracts.events import ConsentDecision, ConsentRequest
from clarion.contracts.state import PageReadout

_AGENT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# --- voice-log fan-out ---------------------------------------------------------
# Worker voice logs go TWO places so BOTH a human and an agent can debug live
# without copy-pasting out of DevTools:
#   1. stdout            → /tmp/clarion-worker.log (the worker's own log; the
#                          cockpit `clarion-up.sh` tags these [worker])
#   2. LiveKit room data → the offscreen doc → service worker → on-page HUD panel
# The worker does NOT POST to the browser sink (/tmp/clarion-ext.log): the cockpit
# tails BOTH worker.log AND ext.log, so a worker line ALSO sent to ext.log showed up
# TWICE (once [worker], once [ext]) — the "duplicated log lines". ext.log is now
# browser-only and the cockpit is the unified view. Both paths never block a turn.


async def _publish_hud(room, entry: dict) -> None:
    """Publish one log entry as LiveKit room data on the `clarion-log` topic. The
    offscreen voice doc forwards it to the service worker, which renders it on the
    on-page HUD (the same path the browser's own `voice.log` lines take)."""
    try:
        await room.local_participant.publish_data(
            json.dumps(entry), reliable=True, topic="clarion-log"
        )
    except Exception as exc:  # noqa: BLE001 - HUD mirroring must never break a turn
        # DEBUG (removable): a publish failure was silently swallowed, which made a
        # missing toast undebuggable from the worker log. Surface it to worker.log
        # (still never raises into a turn). Remove with the offscreen [rx] breadcrumb.
        print(f"  [hud] publish_data FAILED topic=clarion-log: {exc!r}", flush=True)

# LiveKit plugins register their Plugin singletons at import on the MAIN thread
# (the worker forks jobs onto other threads and rejects off-main-thread
# registration). Importing the plugin packages here satisfies that. Wrapped so a
# contracts-only / no-native-dep environment still imports.
try:  # pragma: no cover - import-time plugin registration
    from livekit.plugins import deepgram as _deepgram  # noqa: F401
    from livekit.plugins import minimax as _minimax  # noqa: F401
    from livekit.plugins import silero as _silero  # noqa: F401

    _PLUGINS_OK = True
except Exception as _e:  # noqa: BLE001
    _deepgram = _minimax = _silero = None  # type: ignore[assignment]
    _PLUGINS_OK = False

# `RunContext` / `function_tool` MUST resolve in module globals: with
# `from __future__ import annotations`, LiveKit reads the tool's `RunContext`
# annotation via typing.get_type_hints() against THIS module's globals.
from livekit.agents import RunContext, function_tool  # noqa: E402


# MUST register the turn detector on the main thread at import.
def _register_turn_detector():  # pragma: no cover - best-effort at import
    try:
        from livekit.plugins.turn_detector.multilingual import MultilingualModel

        return MultilingualModel
    except Exception:  # noqa: BLE001
        return None


_MultilingualModel = _register_turn_detector()


# ---------------------------------------------------------------------------
# The stage-graph driver — the seam owner. One per session.
# ---------------------------------------------------------------------------


class StageGraphRunner:
    """Owns the compiled ST1 stage graph + its thread_id and exposes the two seam
    steps the voice tools call: `advance()` (run to the next consent interrupt,
    return the ConsentRequest) and `resume(decision)` (deliver the decision,
    continue to the next interrupt or to END).

    The stage graph re-surfaces the inner kernel's `ConsentRequest` through its OWN
    parent `interrupt()` (the ST1 finding), so the voice plane sees the identical
    `ConsentRequest` at every consequential step — and the irreversible PAY in fast
    mode hard-stops here. Idempotent by construction (the ST1 content-keyed dedup +
    the kernel's ACT once-flag prevent any double-act on a re-delivered resume).
    """

    def __init__(self, runtime=None, *, thread_id: str = "voice-hero") -> None:
        self._runtime = runtime
        # The stage graph bakes in the actuator, so it can only be built once the
        # runtime (and its actuator) exists. In the DECOUPLED entrypoint the runner
        # starts PENDING (runtime=None) so the AgentSession can greet + listen
        # before the tab relay is up; `bind()` builds the graph once it attaches.
        self._graph = runtime.build_stage_graph() if runtime is not None else None
        self._thread_base = thread_id
        self._thread_id = thread_id
        # Bumped when a NEW goal starts a fresh run. A completed goal leaves its
        # graph at END on its thread; the next goal needs a FRESH thread so the
        # checkpointer restores nothing (re-seeding the same thread would replay the
        # END state + concatenate the old audit log). 0 = the first run. See advance().
        self._run_n = 0
        self._seed = None  # set on first advance
        # The user's CONFIRMED goal — NEVER a baked string. Set via set_goal() from
        # what the user told us and confirmed (the agentic clause, applied to
        # goal-setting: no goal assumed without a yes). Empty until then.
        self._goal = ""
        # Last graph-state snapshot, so advance_task can speak an HONEST terminal
        # line (completed vs couldn't-complete) instead of a blanket "task complete".
        self._last_values: Optional[dict] = None
        # How many `trace` events have already been logged, so `_log_trace` only
        # emits the NEW ones each step (the PROPOSE/GATE/EXECUTOR/REPLANNER trace
        # surfaced to /tmp/clarion-worker.log for a debuggable live run).
        self._trace_logged = 0
        # The (proposal_id, status) pairs already emitted to the activity feed, so
        # each lifecycle TRANSITION (proposed → awaiting_yes → done) surfaces once.
        # A fresh run clears it (see advance()).
        self._activity_emitted: set = set()
        # Injected sink for one new/changed ActivityItem (the HUD toast feed,
        # Feature A). None = no-op (every headless test). Wired in entrypoint to the
        # `clarion-log` room-data path. Never raises into a turn.
        self._activity_sink = None
        # Injected ``(phase, detail, level)`` HUD-line sink for the source-node
        # proof surface (the panel row mirroring the live-page highlight). None =
        # no-op (headless tests). Wired in entrypoint to the same `hud()` path.
        self._hud_sink = None

    @property
    def ready(self) -> bool:
        """True once a runtime is bound and the stage graph is built — i.e. the
        tab relay has attached and tab actions are available."""
        return self._graph is not None

    def bind(self, runtime) -> None:
        """Bind the now-attached runtime and build the stage graph. Called from the
        background actuator-attach task once the tab relay is live (decoupled from
        the voice plane, which is already greeting/listening)."""
        self._runtime = runtime
        self._graph = runtime.build_stage_graph()

    @property
    def goal(self) -> str:
        return self._goal

    def set_goal(self, goal: str) -> None:
        """Set the user's CONFIRMED goal — the task we drive toward. Never a baked
        string; the goal is what the user told us and confirmed (foundation §1
        agentic clause, applied to goal-setting)."""
        self._goal = (goal or "").strip()

    @property
    def gave_up(self) -> bool:
        """True if the last run ended at the replanner's bounded give-up — i.e. we
        tried and could NOT complete the goal (so advance_task never claims success
        on a page that didn't afford the task)."""
        for event in (self._last_values or {}).get("trace", []) or []:
            data = getattr(event, "data", None) or {}
            if data.get("gave_up"):
                return True
        return False

    async def describe_page(self) -> PageReadout:
        """ORIENT: a grounded readout of the LIVE page (the screen-reader baseline,
        before any goal). Uses the actuator's whole-page describe when available,
        else summarizes the interactive map — either way every fact is sourced to a
        real AX node (foundation §1)."""
        actuator = self._runtime.actuator
        describe = getattr(actuator, "describe_page", None)
        if describe is not None:
            return await describe()
        sm = await actuator.perceive()
        return readout_from_selector_map(sm)

    def _last_navigation(self) -> Optional[tuple[str, str]]:
        """``(url_before, url_after)`` of the most recent EXECUTOR step, read from
        the captured trace (no extra perceive). ``None`` if there's no such entry."""
        events = (self._last_values or {}).get("trace", []) or []
        for event in reversed(events):
            if getattr(event, "node", "") == "EXECUTOR" and getattr(event, "event", "") == "exit":
                data = getattr(event, "data", None) or {}
                before, after = data.get("url_before"), data.get("url_after")
                if before is not None and after is not None:
                    return (str(before), str(after))
        return None

    async def navigated_readout(self) -> Optional[str]:
        """If the just-completed step changed the page URL, return the NEW page's
        grounded readout — the same content ``read_screen`` would speak — so the
        page content rides the action's completion instead of waiting for a separate
        ``read_screen`` tool call. ``None`` when nothing navigated (the caller then
        speaks its own terminal line). The readout is sourced to real AX nodes, so
        nothing ungrounded is ever spoken (foundation §1). Best-effort: a read error
        degrades to ``None`` rather than crashing the turn."""
        nav = self._last_navigation()
        if not nav or nav[0] == nav[1]:
            return None
        try:
            readout = await self.describe_page()
        except Exception:  # noqa: BLE001 - never crash the turn on a read
            return None
        return readout.summary or None

    async def undo_last(self) -> str:
        """Reverse the most recent step on an explicit "go back" / "undo that" —
        HONEST by construction. We undo ONLY a reversible navigation, by re-navigating
        to the recorded prior URL (we already hold ``url_before``/``url_after`` on the
        EXECUTOR trace; navigate-to-URL is the robust SPA-safe inverse). The reversal
        is a REAL actuator navigate + re-perceive — never a faked "undone". It refuses
        plainly when the last step is irreversible (you can't un-submit), and says so
        when there's simply nothing to undo. The user's spoken "go back" IS the
        consent for this reversible move (so no second yes); never call this to take
        back an irreversible commit."""
        if not self.ready:
            return "I'm still connecting to your tab — give me a moment, then ask again."
        # The last decided action + its GROUNDED reversibility verdict (kernel-
        # computed, read off the recorded trace — never a guess).
        items = self.activity_items()
        last = items[-1] if items else None
        committed = last is not None and last.status in ("done", "approved")
        # Refuse ONLY a KNOWN irreversible commit — you can't un-submit a payment. An
        # ``unknown`` step that merely navigated is still revertible (we re-navigate to
        # url_before); we just hedge that we can't be sure nothing else changed. The
        # tool also requires a real URL change below, so it never claims a phantom undo.
        if committed and last.irreversibility == "irreversible":
            return "I can't undo that one — that step can't be taken back."
        nav = self._last_navigation()
        if not nav or nav[0] == nav[1]:
            return "There's nothing to undo — the last thing I did didn't move the page."
        url_before, _url_after = nav
        from clarion.contracts.state import Action

        try:
            await self._runtime.actuator.act(Action(kind="navigate", value=url_before))
            readout = await self.describe_page()
        except Exception as exc:  # noqa: BLE001 - never crash the turn on the reversal
            return f"I couldn't take us back just now ({exc}). Want me to try again?"
        # A fresh goal re-perceives, so the stale graph page_index self-heals; surface
        # the restored page so the user immediately hears where they are again.
        summary = (readout.summary or "").strip()
        # Honest hedge when the reverted step was only ``unknown`` (we restored the
        # page but can't be sure nothing else changed); a clean line for a reversible.
        if committed and last.irreversibility == "unknown":
            return (
                "I took you back to the previous page. If that step changed anything "
                f"beyond the page, I can't be sure that part is undone. {summary}"
            ).strip()
        return f"Done — I took you back. {summary}".strip()

    @property
    def _cfg(self) -> dict:
        return {"configurable": {"thread_id": self._thread_id}}

    async def _publish(self) -> None:
        """Publish the current PanelState after a graph step (execution §6)."""
        try:
            snap = self._graph.get_state(self._cfg)
            if snap.values:
                await self._runtime.publisher.publish(snap.values)
        except Exception:  # noqa: BLE001 - publishing must never break a turn
            pass

    def _capture_state(self) -> None:
        """Snapshot the graph state after a step so advance_task can speak an honest
        terminal line (see ``gave_up``). Best-effort — never breaks a turn."""
        try:
            self._last_values = self._graph.get_state(self._cfg).values
        except Exception:  # noqa: BLE001
            self._last_values = None

    def _log_trace(self) -> None:
        """Surface each NEW `trace` event since the last step to stdout (which the
        worker pipes to /tmp/clarion-worker.log), so the PLANNER/PROPOSE/GATE/
        EXECUTOR/REPLANNER decisions are FULLY visible in a live run.

        FULL TRACING (no whitelist, no clipping): every `data` field is printed —
        the whole plan, the decide context (verbatim intent + phase + done_check),
        and the model's own `scratch` reasoning — so the behaviour is completely
        traceable. The worker log IS the dev log. Best-effort — never breaks a turn
        (matches the HUD/publish `# noqa: BLE001` pattern)."""
        try:
            events = (self._last_values or {}).get("trace", []) or []
            for event in events[self._trace_logged :]:
                node = getattr(event, "node", "?")
                ev = getattr(event, "event", "info")
                data = getattr(event, "data", None) or {}
                compact = " ".join(f"{k}={v}" for k, v in sorted(data.items()))
                print(f"  [task] {node}.{ev} {compact}".rstrip(), flush=True)
            self._trace_logged = len(events)
        except Exception:  # noqa: BLE001 - trace logging must never break a turn
            pass

    def turn_summary(self) -> str:
        """A compact per-turn latency breakdown read from the last run's trace —
        the planner decode, every step decode (sum + count), and how many replans
        were spent. Pairs with the end-to-end ``advance_ms`` the tool logs so a
        live run shows WHICH leg dominated the consent wait. Best-effort: returns
        ``''`` if the trace can't be read (never breaks a turn)."""
        try:
            events = (self._last_values or {}).get("trace", []) or []
            plan_ms = 0.0
            decide_total = 0.0
            decide_n = 0
            replans = 0
            for event in events:
                node = getattr(event, "node", "")
                data = getattr(event, "data", None) or {}
                if node == "PLANNER" and data.get("plan_ms") is not None:
                    plan_ms = float(data["plan_ms"])
                elif node == "PROPOSE" and data.get("decide_ms") is not None:
                    decide_total += float(data["decide_ms"])
                    decide_n += 1
                elif node == "REPLANNER":
                    replans = max(replans, int(data.get("attempts", 0) or 0))
            return (
                f"plan={plan_ms:.0f}ms decide={decide_total:.0f}ms(x{decide_n}) "
                f"replans={replans}"
            )
        except Exception:  # noqa: BLE001 - a summary read must never break a turn
            return ""

    def activity_items(self) -> list:
        """The live ACTIVITY projection — one record per decided action, folded
        from the real ``trace`` + ``consent_log`` (the action-side analog of the
        source-node panel). This is the GROUNDED source ``read_history`` speaks
        from; it is never the voice LLM's free recollection. Best-effort: any read
        error yields ``[]`` (an honest empty history, never a guess)."""
        try:
            from clarion.instrument.publisher import activity_items

            return activity_items(self._last_values or {})
        except Exception:  # noqa: BLE001 - never crash a turn on a projection read
            return []

    def _emit_activity(self) -> None:
        """Surface each NEW/CHANGED decided action to the injected activity sink
        (the HUD toast feed). Diffs by ``(proposal_id, status)`` so a single action
        emits once per lifecycle transition (proposed → awaiting_yes → done), and a
        consent re-execution can't double-emit. Best-effort — never breaks a turn."""
        if self._activity_sink is None:
            return
        try:
            for item in self.activity_items():
                key = (item.proposal_id, item.status)
                if key in self._activity_emitted:
                    continue
                self._activity_emitted.add(key)
                self._activity_sink(item)
        except Exception:  # noqa: BLE001 - the feed must never break a turn
            pass

    # ---- source-node highlight (the epistemic-clause proof surface) ---------
    # Outlines, on the live page, the SAME node the agent resolved to act — synced
    # to the per-step consent readback (the form-fill Q&A pair is the hero). For
    # SIGHTED observers only: the blind user's channel is the spoken citation, so
    # every call is best-effort / fail-open and the product NEVER depends on it.

    def _hud(self, phase: str, detail: str = "", level: str = "info") -> None:
        """Emit one panel line to the injected HUD sink (no-op if unset)."""
        if self._hud_sink is None:
            return
        try:
            self._hud_sink(phase, detail, level)
        except Exception:  # noqa: BLE001 - the proof surface must never break a turn
            pass

    async def _safe_clear(self) -> None:
        if not self.ready:
            return
        clear = getattr(self._runtime.actuator, "clear_highlight", None)
        if clear is None:
            return
        try:
            await clear()
        except Exception:  # noqa: BLE001 - clearing must never break a turn
            pass

    async def clear_highlight(self) -> None:
        """Remove the live-page outline (idempotent, best-effort)."""
        await self._safe_clear()

    async def _apply_highlight(self, req: ConsentRequest) -> None:
        """A step PARKED at consent: outline its field node on the live page (the
        SAME index the actuator clicks) and mirror the PROVEN field⟷label pairing as
        a HUD panel row. Clears any prior box first (fade-on-next-step). ``req.source``
        is the kernel-built node identity; absent → nothing to point at."""
        await self._safe_clear()
        src = getattr(req, "source", None)
        if src is None or src.index is None or not self.ready:
            return
        hl = getattr(self._runtime.actuator, "highlight", None)
        if hl is not None:
            try:
                await hl(src.index)
            except Exception:  # noqa: BLE001 - the product never depends on the box
                pass
        name = (src.name or "field").strip()
        nid = src.node_id or "?"
        if src.label_text and src.method:
            row = f'{name} (node {nid}) ⟷ "{src.label_text}" via {src.method}'
        else:
            row = f"{name} (node {nid})"
        self._hud("[source]", row, "ok")

    async def _apply_highlight_end(self) -> None:
        """The run reached END: clear any box. For a verified ABSENCE (the two-sided
        proof — the move a screenshot agent can't copy) show the empty-state row.
        Read from the now-committed snapshot (valid at END, unlike at the parked
        interrupt)."""
        await self._safe_clear()
        step = (self._last_values or {}).get("pending_step")
        if step is not None and bool(getattr(step, "asserts_absence", False)):
            self._hud("[source]", "verified absent — nothing to point at", "warn")

    async def advance(self) -> Optional[ConsentRequest]:
        """Run the stage graph to the next consent interrupt. Returns the surfaced
        `ConsentRequest` the agent must speak, or None when the run reaches END."""
        from clarion.stages.graph import seed_stage_state

        # A NEW goal arriving while the graph is at END (the prior goal finished —
        # whether completed or gave-up) must start a FRESH run on a FRESH thread.
        # Otherwise `ainvoke(None)` below resumes a graph already at END (no pending
        # work) → returns no interrupt → advance() returns None → a false "Done." and
        # the new goal is never planned (the single-shot-runner bug). `.next` is the
        # authoritative parked-vs-END signal (the same one `_drive_kernel` uses for
        # the inner kernel): a graph PARKED at a consent interrupt has a non-empty
        # `.next`, so the resume seam (advance → confirm_consent.resume) is untouched.
        if self._seed is not None and not self._graph.get_state(self._cfg).next:
            self._run_n += 1
            self._thread_id = f"{self._thread_base}-{self._run_n}"
            self._seed = None
            self._trace_logged = 0
            self._last_values = None
            self._activity_emitted = set()

        if self._seed is None:
            page = await self._runtime.actuator.perceive()
            # The goal is the user's CONFIRMED intent (set via set_goal) — NOT a
            # hardcoded task. The graph drives toward whatever the user asked for.
            self._seed = seed_stage_state(
                goal=self._goal, mode=self._runtime.mode, page_index=page
            )
            result = await self._graph.ainvoke(self._seed, self._cfg)
        else:
            # `_seed` set AND the graph is parked at a consent interrupt (`.next`
            # non-empty): an advance() here re-surfaces the same interrupt (the
            # resume itself goes through confirm_consent → resume()).
            result = await self._graph.ainvoke(None, self._cfg)
        await self._publish()
        self._capture_state()
        self._log_trace()
        self._emit_activity()
        if "__interrupt__" not in result:
            await self._apply_highlight_end()
            return None
        (intr,) = result["__interrupt__"]
        req = ConsentRequest.model_validate(intr.value)
        await self._apply_highlight(req)
        return req

    async def resume(self, decision: ConsentDecision) -> Optional[ConsentRequest]:
        """Deliver the consent decision; continue to the next interrupt or END.
        Returns the next `ConsentRequest` (if another consequential step is
        reached) or None at END."""
        result = await self._graph.ainvoke(
            Command(resume=decision.model_dump()), self._cfg
        )
        await self._publish()
        self._capture_state()
        self._log_trace()
        self._emit_activity()
        if "__interrupt__" not in result:
            await self._apply_highlight_end()
            return None
        (intr,) = result["__interrupt__"]
        req = ConsentRequest.model_validate(intr.value)
        await self._apply_highlight(req)
        return req


# ---------------------------------------------------------------------------
# The advance_task / confirm_consent function tools (the V1 seam).
# ---------------------------------------------------------------------------


def build_voice_tools(runner: StageGraphRunner):
    """Build the LiveKit `@function_tool`s bound to `runner` (execution §5).

    `read_screen` ORIENTS: reads back what's actually on the live page (grounded in
    the AX tree) so the user knows what's there before any goal is set. `advance_task`
    drives the user's CONFIRMED goal one consequential step NON-BLOCKING (overlapped
    with the spoken sentence; barge-in cancels it cleanly) and returns the consent
    readback to speak. `confirm_consent` delivers the user's "yes"/"no" as a
    Command(resume) inside `disallow_interruptions()` (the atomic act)."""

    @function_tool()
    async def read_screen(context: RunContext) -> str:
        """Read back what's on the user's CURRENT page — grounded in the live
        accessibility tree (its headings and the controls they can use). Call this
        when the user asks what's on the page or what they can do here, or to orient
        yourself before starting a task. Speak the returned summary; add NOTHING
        that isn't in it."""
        if not runner.ready:
            return (
                "I'm still connecting to your tab — give me a moment, then ask again."
            )
        try:
            readout = await runner.describe_page()
        except Exception as exc:  # noqa: BLE001 - never crash the turn on a read
            return f"I couldn't read the page just now ({exc}). Want me to try again?"
        return readout.summary

    @function_tool()
    async def undo_last(context: RunContext) -> str:
        """Take back / reverse the last step when the user says "go back", "undo
        that", or "take it back". Only call this when they ask to reverse what was
        just done. Speak the returned line VERBATIM. It reverses ONLY a reversible
        navigation and refuses honestly when the last step can't be undone — never
        claim to undo something irreversible like a submitted payment."""
        return await runner.undo_last()

    @function_tool()
    async def read_history(context: RunContext, n: int = 3) -> str:
        """Read back the last `n` steps we've actually taken — what was read,
        filled, selected, or is awaiting the user's yes. Call this when the user
        asks what you've done so far, the last few steps, or where they are in the
        task. Pass how many steps they asked for as `n` (default 3). Speak the
        returned summary VERBATIM; it is built from the real recorded trace — add
        NOTHING that isn't in it."""
        from clarion.instrument.publisher import format_history_say

        try:
            count = int(n) if n else 3
        except (TypeError, ValueError):
            count = 3
        # GROUNDED from the recorded trace, never the LLM's recollection — the
        # history has a real source, so it is structurally speakable.
        return format_history_say(runner.activity_items(), count)

    @function_tool()
    async def advance_task(context: RunContext, user_intent: str = "") -> str:
        """Drive the user's CONFIRMED goal one consequential step. Only call this
        AFTER the user has told you what they want and confirmed it. Pass their goal
        as `user_intent`. Speak the returned readback VERBATIM, then wait for yes/no."""
        if not runner.ready:
            # Voice is live but the tab relay hasn't attached yet (decoupled loop).
            # Never fabricate an action — say so plainly (the §-invariant: no action
            # without a connected surface).
            return (
                "I'm still connecting to your tab — give me a moment, then ask again."
            )
        goal = (user_intent or "").strip() or runner.goal
        if not goal:
            # No goal yet — never assume one. Ask for it (the agentic clause applied
            # to goal-setting: no goal without the user telling us).
            return (
                "I don't have a goal yet. Tell me what you'd like to do on this page "
                "and I'll read it back to confirm before I start."
            )
        runner.set_goal(goal)
        _t0 = time.time()
        consent_req = await advance_non_blocking(
            context.speech_handle,
            runner.advance,
            log=lambda m: print(f"  [advance_task] {m}", flush=True),
        )
        # End-to-end consent round-trip (tool-enter → consent-readback ready): the
        # number the user feels as "waiting for consent". The per-leg breakdown
        # (planner decode + step decode(s) + replans) rides alongside so a live run
        # shows which leg dominated.
        print(
            f"  [lat] advance_ms={(time.time() - _t0) * 1000:.0f} "
            f"{runner.turn_summary()}",
            flush=True,
        )
        if consent_req is not None:
            return consent_req.utterance  # the agent speaks this; user answers yes/no
        # No consent surfaced → the run reached END. Be HONEST about which END this
        # is: a bounded give-up means we tried and couldn't (never the old blanket
        # "task complete" on a page that didn't afford the task).
        if runner.gave_up:
            return (
                f"I wasn't able to complete '{goal}' on this page — I didn't find "
                f"what I needed. Want me to read back what's here instead?"
            )
        return f"Done — {goal} is complete."

    @function_tool()
    async def confirm_consent(context: RunContext, approved: bool) -> str:
        """Deliver the user's consent decision to the parked task graph. Wrapped in
        disallow_interruptions so a stray 'um' can't fracture the act (execution §5).
        When the approved step NAVIGATES to a new page, the new page's grounded
        readout is returned (spoken) so the user immediately hears what's there —
        no separate 'want me to read?' → read_screen round-trip."""
        if not runner.ready:
            return "I'm not connected to your tab yet — one moment."
        decision = ConsentDecision(decision="approve" if approved else "reject")
        # The atomic act: a stray "um" must not fracture the consent→act (§5). In
        # livekit-agents 1.5.x this is a plain call (sets allow_interruptions=False
        # on this function-call's speech handle), NOT a context manager.
        context.disallow_interruptions()
        _t0 = time.time()
        next_req = await runner.resume(decision)
        if next_req is not None:
            # Another consequential step surfaced — log this consent leg, then hand
            # back its readback.
            print(
                f"  [lat] consent_ms={(time.time() - _t0) * 1000:.0f} "
                f"{runner.turn_summary()}",
                flush=True,
            )
            return next_req.utterance  # next consequential step's readback
        # END. If this step navigated to a NEW page, auto-inject that page's grounded
        # readout so the page content rides the consent completion (no extra
        # read_screen call). No navigation → the plain terminal line.
        print(
            f"  [lat] consent_ms={(time.time() - _t0) * 1000:.0f} "
            f"{runner.turn_summary()}",
            flush=True,
        )
        # HONEST END (the same check advance_task makes at its END): a bounded
        # give-up is NOT a success — never speak "Done." over a run that tried and
        # could not finish. (Live 06-11 run: the kernel gave up on the search
        # subgoal, this returned "Done.", and the voice plane then fabricated
        # "Search submitted" on top of it.)
        if runner.gave_up:
            return (
                f"I wasn't able to finish '{runner.goal}' on this page — I didn't "
                f"find what I needed. Want me to read back what's here instead?"
            )
        readout = await runner.navigated_readout()
        if readout is not None:
            return readout
        return "Done."

    return [read_screen, read_history, undo_last, advance_task, confirm_consent]


# ---------------------------------------------------------------------------
# The worker entrypoint.
# ---------------------------------------------------------------------------

_INSTRUCTIONS = (
    "You are Clarion, the eyes and hands on the web for a person who is BLIND or "
    "low-vision and cannot see the screen at all. They are an expert who drives "
    "screen readers every day — speak to them as a capable adult in command, in "
    "short declarative sentences, never with deference, apology, or hand-holding. "
    "The website may be broken; they are not. Your job is to get them where they "
    "want to go and their task done — fast, plain, eyes-free.\n\n"
    "BE TASK-DRIVEN, NOT A TOUR GUIDE. Lead with what serves what they asked. When "
    "they tell you where to go or what to do ('pay my Social Security', 'make a loan "
    "payment'), GET THEM THERE: orient if you need to, then navigate or act. If their "
    "target isn't on this page, find the route to it — open the right section, search, "
    "or browse — instead of reading back whatever happens to be here. Never recite the "
    "whole page; surface the few things relevant to their goal, then move.\n\n"
    "ORIENT — When you need to know the page (or they ask what's here), call "
    "read_screen. Speak only what's relevant to their goal, a sentence or two; if they "
    "want the full list, they'll ask. If it says something isn't there, say so plainly "
    "— never guess.\n\n"
    "SET THE GOAL — Put their goal in one short sentence from what they said plus "
    "what's on the page, then go STRAIGHT to advance_task with it as user_intent. Don't "
    "ask them to confirm the goal first — the goal is theirs.\n\n"
    "ACT — Call advance_task and speak the readback it returns VERBATIM. That readback "
    "is the single confirmation. If it asks for their yes — a CONSEQUENTIAL, "
    "irreversible step like submitting a payment, sending money, or confirming an order "
    "— wait for yes or no, then call confirm_consent (approved=true for yes, false for "
    "no). If it just reports a reversible move (a page opened, a section reached, "
    "something read), keep going toward their goal. Do NOT add your own yes/no question "
    "or an 'I can't undo this' warning on top of ordinary navigation or reading, and do "
    "NOT stack repeated warnings. One clear yes, only at the moment that truly matters. "
    "NEVER take an irreversible step without an explicit yes.\n\n"
    "Read grounded facts (amount, payee, due date, fees) and cite where you read them; "
    "say plainly when something isn't there. Be concise; no emojis or markdown.\n\n"
    "HISTORY — When they ask what you've done or where they are, call read_history and "
    "speak its summary VERBATIM — the real recorded steps, never your own memory.\n\n"
    "GO BACK — When they say 'go back', 'undo that', or 'take it back', call undo_last "
    "and speak its result VERBATIM. It reverses only a reversible move and tells them "
    "plainly when a step can't be taken back — never promise to undo something "
    "irreversible."
)


# Background tasks (tab-attach + real-sim) outlive the entrypoint body; keep a
# strong reference so the event loop can't GC them mid-flight.
_BG_TASKS: set = set()


def _spawn(coro):
    task = asyncio.ensure_future(coro)
    _BG_TASKS.add(task)
    task.add_done_callback(_BG_TASKS.discard)
    return task


async def entrypoint(ctx) -> None:
    """LiveKit worker entrypoint (execution §5), DECOUPLED. The AgentSession starts
    and greets IMMEDIATELY on dispatch — the agent hears the user right away — while
    the chrome.debugger tab-relay attaches and the ST1 stage-graph runner binds in
    the BACKGROUND. Voice never waits on the tab: `advance_task` says "connecting to
    your tab" until the runner is ready. This removes the old coupling where the
    agent's ears only turned on AFTER the relay attached. Every phase is logged with
    a `[loop]` prefix so the whole post-shortcut loop is observable in one place."""
    from livekit.agents import (
        Agent,
        AgentSession,
        UserInputTranscribedEvent,
    )

    from clarion.adapters.minimax_synthesizer import MinimaxSynthesizer
    from clarion.app.extension_runtime import extension_actuator_selected
    from clarion.app.runtime import HeroRuntime

    def _fanout(line: str) -> None:
        """Worker log line → stdout (/tmp/clarion-worker.log). Deliberately NOT the
        browser sink: the cockpit tails worker.log AND ext.log, so POSTing here
        double-logged every worker line. ext.log is browser-only now; the HUD still
        gets IMPORTANT lines via the clarion-log topic (see `hud()`)."""
        print(f"  {line}", flush=True)

    def loop(msg: str) -> None:
        """One observable line per loop phase → worker.log + the unified ext.log."""
        _fanout(f"[loop] {msg}")

    def hud(phase: str, detail: str = "", level: str = "info") -> None:
        """An IMPORTANT voice-conversation line: worker.log + the unified ext.log
        AND the on-page extension HUD panel (room data → offscreen → service
        worker → overlay), so the whole turn is visible without DevTools."""
        _fanout(f"{level.upper()} | {phase}" + (f" | {detail}" if detail else ""))
        if getattr(ctx, "room", None) is not None:
            _spawn(_publish_hud(ctx.room, {"phase": phase, "detail": detail, "level": level}))

    await ctx.connect()
    loop("dispatched + connected to the room")

    # The stage-graph runner starts PENDING — bound once the tab relay attaches, so
    # the AgentSession below can greet + listen before the tab is up.
    runner = StageGraphRunner()
    tools = build_voice_tools(runner)

    # Feature A — the action-trace feed. Each NEW/changed decided action is
    # published over the SAME `clarion-log` room-data path the HUD already consumes;
    # the offscreen doc routes frames carrying an `activity` payload to the on-page
    # toast feed + the panel's Activity section. Fire-and-forget — never blocks a turn.
    def _emit_activity_frame(item) -> None:
        if getattr(ctx, "room", None) is None:
            return
        status = getattr(item, "status", "")
        level = (
            "err"
            if status in ("failed", "rejected")
            else "warn"
            if (status == "awaiting_yes" or getattr(item, "persist", False))
            else "ok"
            if status == "done"
            else "info"
        )
        frame = {
            "phase": "[activity]",
            "detail": (f"{item.kind} {item.target}".strip() or item.proposal_id),
            "level": level,
            "activity": item.model_dump(),
        }
        # PROVE emission on the worker side (the publish path is otherwise silent —
        # it never printed, so a missing toast was undebuggable from the worker log).
        print(
            f"  [activity] published {item.kind} {item.target!r} "
            f"status={status} irr={item.irreversibility}",
            flush=True,
        )
        _spawn(_publish_hud(ctx.room, frame))

    runner._activity_sink = _emit_activity_frame
    # Source-node proof surface: the panel ROW mirroring the live-page highlight
    # rides the SAME `hud()` → `clarion-log` path (the live-page box is drawn over
    # the actuator relay, separately). Best-effort; never blocks a turn.
    runner._hud_sink = hud

    # Contract-correct TTS the kernel sees (MiniMax Speech 2.6, streaming PCM);
    # constructed so the wiring is genuine even though the audio path uses the
    # LiveKit minimax.TTS plugin below.
    _synth = MinimaxSynthesizer()  # noqa: F841 - lazy httpx client

    vad = ctx.proc.userdata.get("vad") if hasattr(ctx, "proc") else None
    # Flux owns turn detection ("stt"); nova-3 fallback returns the EOU detector.
    _stt, _turn_detection = _build_stt()
    session = AgentSession(
        stt=_stt,
        # MiniMax-M3 via the MiniMax Anthropic gateway (LiveKit `anthropic` plugin).
        llm=_build_llm(),
        # Voice = LiveKit Inference (native; no per-provider key) — Cartesia Sonic-2
        # default + Deepgram Aura-2 failover. Override with CLARION_TTS_MODEL/_VOICE.
        tts=_build_audio_tts(),
        vad=vad or _silero.VAD.load(),
        turn_detection=_turn_detection,
    )
    agent = Agent(instructions=_INSTRUCTIONS, tools=tools)
    # Surface the live voice so "is the agent speaking?" is answerable from the log
    # + HUD (the prior MiniMax path went silent without saying which model was up).
    _tts_fb = os.environ.get("CLARION_TTS_FALLBACK", "deepgram/aura-2")
    hud(
        "[tts] voice",
        f"LiveKit Inference · {os.environ.get('CLARION_TTS_MODEL', 'cartesia/sonic-2')}"
        + (f" → {_tts_fb}" if _tts_fb.lower() != "off" else ""),
        "ok",
    )
    # Surface which ASR is up + whether it's driving turn detection (Flux) — so a
    # "bad transcript" can be triaged to model vs. turn-segmentation from the HUD.
    _stt_model = os.environ.get("STT_MODEL", "flux-general-en")
    hud(
        "[asr] model",
        f"Deepgram {_stt_model}"
        + (" · native turn-detect" if _stt_model.startswith("flux") else ""),
        "ok",
    )

    # ───────────────────────── voice-conversation observability ─────────────────
    # The WHOLE turn is logged: what the mic/STT HEARD, the agent's state machine
    # (listening → thinking[LLM] → speaking[TTS]), each conversation item, every
    # tool call + output, latency metrics, and errors. IMPORTANT lines go to the
    # on-page HUD panel via hud(); high-frequency lines (partials, metrics) stay in
    # the files via loop(). Grep [asr]/[agent]/[tool]/[error] in the worker log or
    # the unified /tmp/clarion-ext.log.

    @session.on("user_input_transcribed")
    def _on_heard(ev: UserInputTranscribedEvent) -> None:
        if getattr(ev, "is_final", False):
            hud("[asr] HEARD ✓", repr(ev.transcript), "ok")  # mic → STT confirmed
        # else: partial transcripts are high-frequency noise — silenced. Re-enable
        # `loop(f"[asr] heard… partial: {ev.transcript!r}")` here for STT debugging.

    # 'speaking'/'listening' toggles fire on every VAD edge — high-frequency noise.
    # Silenced (handler left unregistered). Re-enable by re-adding the @session.on
    # below and `hud("[asr] user", ev.new_state)` for VAD-vs-STT debugging.

    @session.on("agent_state_changed")
    def _on_agent_state(ev) -> None:  # noqa: ANN001 - loosely typed LiveKit event
        # initializing → listening → thinking (LLM decode) → speaking (TTS).
        old, new = getattr(ev, "old_state", "?"), getattr(ev, "new_state", "?")
        hud("[agent]", f"{old} → {new}", "info")

    @session.on("conversation_item_added")
    def _on_item(ev) -> None:  # noqa: ANN001
        item = getattr(ev, "item", None)
        role = getattr(item, "role", "?")
        text = (getattr(item, "text_content", "") or "").strip()
        if text:
            # Full tracing: don't clip the spoken turn (the worker log is the dev log).
            hud(f"[turn] {role}", text, "info")

    @session.on("function_tools_executed")
    def _on_tools(ev) -> None:  # noqa: ANN001 - the LLM's tool decisions this turn
        for fc in getattr(ev, "function_calls", None) or []:
            name = getattr(fc, "name", "?")
            args = getattr(fc, "arguments", "")
            # Full tracing: the whole tool call + its whole result, un-clipped.
            hud("[tool] →", f"{name}({args})", "info")
        for out in getattr(ev, "function_call_outputs", None) or []:
            hud("[tool] ←", str(getattr(out, "output", "")), "ok")

    # Voice-leg latency (the OTHER half of the consent wait, alongside the task
    # plane's [lat] advance_ms/plan_ms/decide_ms): the LLM time-to-first-token, the
    # TTS time-to-first-byte, and the end-of-utterance / transcription delay. We log
    # ONLY these meaningful fields to /tmp via loop() — per-frame VADMetrics and the
    # STTMetrics duration=0.00 spam stay silenced (they were the bulk of the noise).
    @session.on("metrics_collected")
    def _on_metrics(ev) -> None:  # noqa: ANN001 - loosely typed LiveKit metrics event
        m = getattr(ev, "metrics", None)
        if m is None:
            return
        kind = type(m).__name__

        def _ms(attr: str) -> float:
            v = getattr(m, attr, None)
            try:
                return float(v) * 1000.0 if v is not None else 0.0
            except (TypeError, ValueError):
                return 0.0

        if kind == "LLMMetrics":  # voice-plane LLM (MiniMax-M3) decode
            loop(f"[lat] voice-llm ttft={_ms('ttft'):.0f}ms dur={_ms('duration'):.0f}ms")
        elif kind == "TTSMetrics":  # LiveKit Inference (Cartesia/Deepgram) synth
            loop(f"[lat] voice-tts ttfb={_ms('ttfb'):.0f}ms dur={_ms('duration'):.0f}ms")
        elif kind == "EOUMetrics":  # turn-detect + STT finalize before the LLM fires
            loop(
                f"[lat] turn-eou eou={_ms('end_of_utterance_delay'):.0f}ms "
                f"stt={_ms('transcription_delay'):.0f}ms"
            )
        # STTMetrics / VADMetrics: per-frame spam — deliberately not logged.

    @session.on("error")
    def _on_error(ev) -> None:  # noqa: ANN001 - LLM/TTS/STT failures surface here
        src = getattr(ev, "source", "")
        hud("[error]", f"{src}: {getattr(ev, 'error', ev)!r}"[:240], "err")

    @session.on("close")
    def _on_close(ev) -> None:  # noqa: ANN001
        hud("[close]", f"reason={getattr(ev, 'reason', None)}", "warn")

    # *** The agent's ears turn ON here — BEFORE the tab relay. Speak → heard. ***
    await session.start(agent=agent, room=ctx.room)
    loop("AgentSession STARTED — listening now (no tab required to talk)")

    # Attach the tab surface in the BACKGROUND; bind the runner when it's live.
    demo_url = os.environ.get("DEMO_SITE_URL", "http://localhost:8770/")
    # Consent mode (kernel ``consent_gate``): "fast" auto-proceeds ONLY a step the
    # IrreversibilityGate classified REVERSIBLE (and only under ``fast_act_cap``
    # silent acts — then it forces a spoken beat); ``unknown``/``irreversible``
    # ALWAYS gate, so the hard-stop invariant is untouched. "normal" gates every
    # consequential step — the live 06-11 run burned three "say yes" round-trips
    # before the task even ran (a search-box fill spoken as "treat as final").
    # Default fast = the demo pacing; CLARION_CONSENT_MODE=normal to restore.
    consent_mode = (
        "normal"
        if os.environ.get("CLARION_CONSENT_MODE", "fast").strip().lower() == "normal"
        else "fast"
    )

    async def attach_tab() -> None:
        try:
            if extension_actuator_selected():
                from clarion.app.extension_runtime import ExtensionRuntime

                ext = ExtensionRuntime(demo_url=demo_url, mode=consent_mode, room=ctx.room)
                # The tab bridge is the ALWAYS-ON broker (started by clarion-up),
                # NOT a port we bind here — that's what decouples it from voice.
                # We dial the broker as a client and wait for the tab to attach.
                await ext.attach_broker()
                loop("connected to relay broker — waiting for the extension to attach the tab…")
                await ext.wait_for_session(timeout=None)
                loop("extension attached the tab — building the stage graph…")
                runtime = await ext.build_runtime()
            else:
                runtime = await HeroRuntime.create(
                    demo_url, mode=consent_mode, room=ctx.room, headless=True
                )
            runner.bind(runtime)
            loop("stage-graph runner READY — tab actions enabled")
        except Exception as exc:  # noqa: BLE001 - the voice plane must survive this
            loop(f"tab attach FAILED (voice still works): {exc!r}")

    _spawn(attach_tab())

    # Greet, then (optionally) drive the REAL-SIM — both in ONE background task so
    # the entrypoint returns while the session keeps running, and the greeting is
    # awaited first so a scripted "user" turn never collides with it.
    #
    # REAL-SIM (no fakes): scripted user turns driven AS TEXT through the real LLM +
    # tools + TTS — "speaking" via text input; the only un-real link is mic→STT.
    #   CLARION_SIM_UTTERANCES="pay my electric bill|yes"   CLARION_SIM_GAP=4
    sim = os.environ.get("CLARION_SIM_UTTERANCES", "").strip()
    loop(f"sim armed = {bool(sim)} ({sim!r})")

    async def greet_then_sim() -> None:
        try:
            await session.generate_reply(
                instructions=(
                    "Greet the user briefly: say you're Clarion and you can read back "
                    "what's on their current page and walk them through a task, step "
                    "by step, with their confirmation before anything irreversible. "
                    "Then ask what they'd like to do. Do NOT assume a specific task."
                )
            )
        except Exception as exc:  # noqa: BLE001
            loop(f"greet failed: {exc!r}")
        if not sim:
            return
        loop("[SIM] scripted run starting")
        gap = float(os.environ.get("CLARION_SIM_GAP", "4"))
        for utt in [u.strip() for u in sim.split("|") if u.strip()]:
            await asyncio.sleep(gap)
            loop(f"[SIM] user (text-as-speech): {utt!r}")
            try:
                await session.generate_reply(user_input=utt)
            except Exception as exc:  # noqa: BLE001
                loop(f"[SIM] generate_reply failed: {exc!r}")
        loop("[SIM] scripted utterances complete")

    _spawn(greet_then_sim())


def _build_stt():
    """STT + its turn-detection strategy, as a `(stt, turn_detection)` pair.

    Default = Deepgram **Flux** (`STTv2`, the `wss://api.deepgram.com/v2/listen`
    socket) — Deepgram's real-time-AGENT ASR (their nova-3 is positioned for
    meetings/captioning, not interactive turns). Flux has a model-native phrase-
    endpointing model that calls end-of-turn from acoustic + semantic cues, so it
    OWNS turn detection (`turn_detection="stt"`) and REPLACES both the separate
    `MultilingualModel` EOU detector and the nova-3 `endpointing_ms` pause-tuning
    hack (the source of mid-word finals). VAD still runs for barge-in/interruption.
    EN-only by design → `flux-general-en` (`flux-general-multi` takes a
    `language_hint`; Flux has no `smart_format`).

    Set `STT_MODEL=nova-3` (or any non-`flux*` model) to fall back to the prior
    path: `STT` + `smart_format` + `STT_ENDPOINTING_MS` + the EOU turn detector.

    Env: STT_MODEL (default `flux-general-en`) · STT_EAGER_EOT (0.3–0.9 enables
    preemptive generation; unset = off) · STT_LANGUAGE / STT_ENDPOINTING_MS
    (nova-path only) · CLARION_STT_KEYTERMS (comma-separated terms to BOOST —
    proper nouns the demo task hinges on, e.g. "Point Reyes"; supported by BOTH
    Flux and nova-3 as Deepgram keyterm prompting).
    """
    model = os.environ.get("STT_MODEL", "flux-general-en")
    # Keyterm prompting: the user can't be expected to be heard perfectly — the
    # live 06-11 run transcribed "Point Reyes" as "Ponteries" and the whole task
    # plane chased the garbage string. Boosting the demo task's proper nouns is a
    # recognition-accuracy config (per-deploy, set in clarion-up.sh / .env), NOT a
    # semantic keyword list: nothing here classifies, ranks, or routes meaning.
    keyterm = [
        t.strip()
        for t in os.environ.get("CLARION_STT_KEYTERMS", "").split(",")
        if t.strip()
    ]
    if model.startswith("flux"):
        kw = {}
        eager = os.environ.get("STT_EAGER_EOT", "").strip()
        if eager:  # opt-in preemptive generation (speculative LLM before EOT)
            kw["eager_eot_threshold"] = float(eager)
        if keyterm:
            kw["keyterm"] = keyterm
        stt = _deepgram.STTv2(model=model, api_key=os.environ["DEEPGRAM_API_KEY"], **kw)
        return stt, "stt"

    stt = _deepgram.STT(
        model=model,
        # keyterm boosting (nova-3 only — the plugin validates); [] = NOT_GIVEN-ish
        # is not accepted on this ctor, so pass only when set.
        **({"keyterm": keyterm} if keyterm else {}),
        # Single-stream Deepgram can't code-switch EN+Chinese: `multi` excludes
        # Chinese, Chinese needs a dedicated `zh-*` model. Default en-US (the demo
        # language); set STT_LANGUAGE=zh-CN for Mandarin, =multi for EN+EU/JA.
        language=os.environ.get("STT_LANGUAGE", "en-US"),
        # smart_format = punctuation + dates/numbers/currency formatting (the
        # gov-form domain: amounts, SSNs, dates) — cleaner text for the LLM.
        smart_format=True,
        # endpointing = ms of trailing silence before a segment is finalized. The
        # plugin default (25ms) finalizes on micro-pauses, so halting speech split
        # mid-word ("the s" / "r model") into many tiny finals. Raising it keeps a
        # brief mid-sentence pause from cutting the utterance; the EOU turn detector
        # still decides the real end of turn. Tune via STT_ENDPOINTING_MS.
        endpointing_ms=int(os.environ.get("STT_ENDPOINTING_MS", "300")),
        api_key=os.environ["DEEPGRAM_API_KEY"],
    )
    return stt, (_MultilingualModel() if _MultilingualModel else None)


def _build_llm():
    """The voice LLM: MiniMax M-series via the **Anthropic-compatible gateway**
    (`https://api.minimax.io/anthropic`), using the LiveKit `anthropic` plugin.

    Why the Anthropic gateway, not the OpenAI `/v1` one:
    - NATIVE THINKING: `MiniMax-M3` is a reasoning model. On the OpenAI gateway its
      reasoning leaks as `<think>…</think>` into `content`, and the `reasoning_split`
      workaround returned EMPTY spoken content. The Anthropic Messages API returns
      reasoning as first-class `thinking` blocks; the plugin's stream parser only
      emits `text_delta` (the answer) + tool calls and drops `thinking_delta`, so the
      reasoning is NEVER spoken — no monkeypatch, no empty-content edge.
    - The intermittent `500 "unknown error (1000)"` is a MiniMax-side 5xx under load
      (both gateways front the same models). `FallbackAdapter` cushions a single-model
      blip; a whole-backend wobble still needs a real provider fallback (future).

    Env: MINIMAX_API_KEY (auth) · MINIMAX_ANTHROPIC_BASE_URL (gateway) ·
    MINIMAX_LLM_MODEL (default `MiniMax-M3`) · MINIMAX_LLM_MODEL_FALLBACK
    (default `MiniMax-M2.7`, `off` to disable) · MINIMAX_LLM_MAX_TOKENS (default 2048
    — the plugin's 1024 default is shared with M3's thinking budget and can truncate
    answers) · MINIMAX_LLM_ATTEMPT_TIMEOUT."""
    from livekit.plugins import anthropic

    api_key = os.environ["MINIMAX_API_KEY"]
    base_url = os.environ.get("MINIMAX_ANTHROPIC_BASE_URL", "https://api.minimax.io/anthropic")
    max_tokens = int(os.environ.get("MINIMAX_LLM_MAX_TOKENS", "2048"))

    def _mk(model: str):
        return anthropic.LLM(
            model=model, api_key=api_key, base_url=base_url, max_tokens=max_tokens
        )

    primary_model = os.environ.get("MINIMAX_LLM_MODEL", "MiniMax-M3")
    primary = _mk(primary_model)

    # RESILIENCE: M3's endpoint intermittently 5xx's under load; fail a hard,
    # pre-stream M3 failure OVER to a sibling model so the agent never goes silent.
    # Failover only fires before the first token (retry_on_chunk_sent defaults False),
    # so a mid-sentence M3 is never restarted. Disable with MINIMAX_LLM_MODEL_FALLBACK=off.
    fb_model = os.environ.get("MINIMAX_LLM_MODEL_FALLBACK", "MiniMax-M2.7").strip()
    if not fb_model or fb_model.lower() in ("off", "none") or fb_model == primary_model:
        return primary

    from livekit.agents import llm as _llm

    attempt_timeout = float(os.environ.get("MINIMAX_LLM_ATTEMPT_TIMEOUT", "12"))
    return _llm.FallbackAdapter([primary, _mk(fb_model)], attempt_timeout=attempt_timeout)


def _build_audio_tts():
    """The LiveKit audio-output TTS, via **LiveKit Inference** — the native path.

    Inference routes synthesis through the LiveKit Cloud project's OWN credentials
    (LIVEKIT_API_KEY/SECRET, already in agent/.env), so there is no per-provider
    API key and no MiniMax dependency. Model + voice come from env with a high-
    performance default — **Cartesia Sonic-2**, LiveKit's recommended low-latency
    TTS — and an automatic **Deepgram Aura-2** failover, mirroring the LLM's
    `FallbackAdapter` so a Cartesia hiccup degrades the voice instead of going
    silent. `voice` is optional (provider default if unset); override either knob:

        CLARION_TTS_MODEL     e.g. cartesia/sonic-2 | deepgram/aura-2 | elevenlabs/eleven_turbo_v2_5
        CLARION_TTS_VOICE     provider voice (e.g. deepgram 'athena'); empty → provider default
        CLARION_TTS_FALLBACK  failover model id, or 'off' to disable

    This replaced the MiniMax `minimax.TTS` plugin (+ a per-sentence one-segment
    workaround for the plugin-1.2.9 × agents-1.5.15 `start_segment()` crash);
    Inference uses the native agents-1.5.15 streaming API, so no workaround is needed."""
    from livekit.agents import inference

    model = os.environ.get("CLARION_TTS_MODEL", "cartesia/sonic-2")
    voice = os.environ.get("CLARION_TTS_VOICE", "")
    fallback = os.environ.get("CLARION_TTS_FALLBACK", "deepgram/aura-2")
    kwargs: dict = {"model": model}
    if voice:
        kwargs["voice"] = voice
    if fallback and fallback.lower() != "off":
        kwargs["fallback"] = fallback
    return inference.TTS(**kwargs)


def prewarm(proc) -> None:
    """Load Silero VAD into the job process before assignment (LiveKit prewarm)."""
    if _silero is not None:
        proc.userdata["vad"] = _silero.VAD.load()


def main() -> None:
    """Console/dev runner. `python -m clarion.app.voice_entry console` for the
    LiveKit text/voice console; `dev` to connect to a room."""
    from dotenv import load_dotenv

    load_dotenv(os.path.join(_AGENT_ROOT, ".env"))
    from livekit.agents import WorkerOptions, cli

    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint, prewarm_fnc=prewarm))


__all__ = [
    "StageGraphRunner",
    "build_voice_tools",
    "entrypoint",
    "prewarm",
    "main",
]


if __name__ == "__main__":
    main()
