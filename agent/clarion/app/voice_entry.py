"""I1 — the LiveKit worker entrypoint (execution §1, §5).

The production voice plane for the hero flow. Wires the V1 transport
(`LiveKitVoiceTransport`: live Deepgram STT + Gemini LLM + AI-Studio Gemini TTS +
Silero VAD + MultilingualModel turn detection) over an `AgentSession`, and attaches
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
(shared with the GATE harness — ONE implementation). The TTS is the reconciled
`VertexExpressSynthesizer` defaulting to **AI Studio** mode (the live GOOGLE_API_KEY;
the AQ.* Vertex Express key is billing-blocked — execution §18.6, I1 reconcile).

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
import os
from typing import Optional

from langgraph.types import Command

from clarion.actuator.pipeline import readout_from_selector_map
from clarion.adapters.voice_livekit import advance_non_blocking
from clarion.contracts.events import ConsentDecision, ConsentRequest
from clarion.contracts.state import PageReadout

_AGENT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# LiveKit plugins register their Plugin singletons at import on the MAIN thread
# (the worker forks jobs onto other threads and rejects off-main-thread
# registration). Importing the plugin packages here satisfies that. Wrapped so a
# contracts-only / no-native-dep environment still imports.
try:  # pragma: no cover - import-time plugin registration
    from livekit.plugins import deepgram as _deepgram  # noqa: F401
    from livekit.plugins import google as _google  # noqa: F401
    from livekit.plugins import silero as _silero  # noqa: F401

    _PLUGINS_OK = True
except Exception as _e:  # noqa: BLE001
    _deepgram = _google = _silero = None  # type: ignore[assignment]
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
        self._thread_id = thread_id
        self._seed = None  # set on first advance
        # The user's CONFIRMED goal — NEVER a baked string. Set via set_goal() from
        # what the user told us and confirmed (the agentic clause, applied to
        # goal-setting: no goal assumed without a yes). Empty until then.
        self._goal = ""
        # Last graph-state snapshot, so advance_task can speak an HONEST terminal
        # line (completed vs couldn't-complete) instead of a blanket "task complete".
        self._last_values: Optional[dict] = None

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

    async def advance(self) -> Optional[ConsentRequest]:
        """Run the stage graph to the next consent interrupt. Returns the surfaced
        `ConsentRequest` the agent must speak, or None when the run reaches END."""
        from clarion.stages.graph import seed_stage_state

        if self._seed is None:
            page = await self._runtime.actuator.perceive()
            # The goal is the user's CONFIRMED intent (set via set_goal) — NOT a
            # hardcoded task. The graph drives toward whatever the user asked for.
            self._seed = seed_stage_state(
                goal=self._goal, mode=self._runtime.mode, page_index=page
            )
            result = await self._graph.ainvoke(self._seed, self._cfg)
        else:
            # An advance after a fresh turn with no pending interrupt is a no-op
            # (the graph is parked at an interrupt awaiting resume).
            result = await self._graph.ainvoke(None, self._cfg)
        await self._publish()
        self._capture_state()
        if "__interrupt__" not in result:
            return None
        (intr,) = result["__interrupt__"]
        return ConsentRequest.model_validate(intr.value)

    async def resume(self, decision: ConsentDecision) -> Optional[ConsentRequest]:
        """Deliver the consent decision; continue to the next interrupt or END.
        Returns the next `ConsentRequest` (if another consequential step is
        reached) or None at END."""
        result = await self._graph.ainvoke(
            Command(resume=decision.model_dump()), self._cfg
        )
        await self._publish()
        self._capture_state()
        if "__interrupt__" not in result:
            return None
        (intr,) = result["__interrupt__"]
        return ConsentRequest.model_validate(intr.value)


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
        consent_req = await advance_non_blocking(
            context.speech_handle,
            runner.advance,
            log=lambda m: print(f"  [advance_task] {m}", flush=True),
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
        disallow_interruptions so a stray 'um' can't fracture the act (execution §5)."""
        if not runner.ready:
            return "I'm not connected to your tab yet — one moment."
        decision = ConsentDecision(decision="approve" if approved else "reject")
        # The atomic act: a stray "um" must not fracture the consent→act (§5). In
        # livekit-agents 1.5.x this is a plain call (sets allow_interruptions=False
        # on this function-call's speech handle), NOT a context manager.
        context.disallow_interruptions()
        next_req = await runner.resume(decision)
        if next_req is None:
            return "Done."
        return next_req.utterance  # next consequential step's readback

    return [read_screen, advance_task, confirm_consent]


# ---------------------------------------------------------------------------
# The worker entrypoint.
# ---------------------------------------------------------------------------

_INSTRUCTIONS = (
    "You are Clarion, a voice web co-pilot for someone who cannot see the screen. "
    "You keep them in command and never act without their explicit yes. You NEVER "
    "assume what they want: you ORIENT first, then CONFIRM a goal, then ACT.\n\n"
    "ORIENT — When they ask what's on the page or what they can do here, or whenever "
    "you need to know the page before acting, call read_screen and speak its summary. "
    "Say only what it returns; if it says something isn't there, say so — never guess.\n\n"
    "CONFIRM THE GOAL — From what they say plus what's actually on the page, restate "
    "their goal in one short sentence and ask them to confirm before you start. The "
    "goal comes from them, never from you.\n\n"
    "ACT — Once they confirm a goal, call advance_task with that goal as user_intent. "
    "Speak the readback it returns VERBATIM, then wait for yes or no. When they answer, "
    "call confirm_consent with approved=true for yes or approved=false for no. NEVER "
    "take an irreversible step (like a payment) without an explicit yes.\n\n"
    "Read grounded facts (amount, payee, due date, fees) and cite what you read. "
    "Be concise; no emojis or markdown."
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
    from livekit.agents import Agent, AgentSession

    from clarion.adapters.tts_vertex import VertexExpressSynthesizer
    from clarion.app.extension_runtime import extension_actuator_selected
    from clarion.app.runtime import HeroRuntime

    def loop(msg: str) -> None:
        """One observable line per loop phase — tail /tmp/clarion-worker.log."""
        print(f"  [loop] {msg}", flush=True)

    await ctx.connect()
    loop("dispatched + connected to the room")

    # The stage-graph runner starts PENDING — bound once the tab relay attaches, so
    # the AgentSession below can greet + listen before the tab is up.
    runner = StageGraphRunner()
    tools = build_voice_tools(runner)

    # Contract-correct TTS the kernel sees (AI Studio by default — the live key);
    # constructed so the wiring is genuine even though the audio path uses google.beta.
    _synth = VertexExpressSynthesizer()  # noqa: F841 - mode defaults to ai_studio

    vad = ctx.proc.userdata.get("vad") if hasattr(ctx, "proc") else None
    session = AgentSession(
        stt=_deepgram.STT(
            model=os.environ.get("STT_MODEL", "nova-3"),
            language="en-US",
            api_key=os.environ["DEEPGRAM_API_KEY"],
        ),
        llm=_google.LLM(
            model=os.environ.get("GEMINI_MODEL", "gemini-3.5-flash"),
            api_key=os.environ["GOOGLE_API_KEY"],
        ),
        tts=_build_audio_tts(),
        vad=vad or _silero.VAD.load(),
        turn_detection=_MultilingualModel() if _MultilingualModel else None,
    )
    agent = Agent(instructions=_INSTRUCTIONS, tools=tools)

    # *** The agent's ears turn ON here — BEFORE the tab relay. Speak → heard. ***
    await session.start(agent=agent, room=ctx.room)
    loop("AgentSession STARTED — listening now (no tab required to talk)")

    # Attach the tab surface in the BACKGROUND; bind the runner when it's live.
    demo_url = os.environ.get("DEMO_SITE_URL", "http://localhost:8770/")

    async def attach_tab() -> None:
        try:
            if extension_actuator_selected():
                from clarion.app.extension_runtime import ExtensionRuntime

                ext = ExtensionRuntime(demo_url=demo_url, mode="fast", room=ctx.room)
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
                    demo_url, mode="fast", room=ctx.room, headless=True
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


def _build_audio_tts():
    """The LiveKit audio-output TTS plugin. NOTE: the google.beta.GeminiTTS plugin
    routes through the SDK's own auth; the kernel-facing live TTS is the AI-Studio
    `VertexExpressSynthesizer`. Model + voice come from env (no swaps)."""
    from livekit.plugins import google

    return google.beta.GeminiTTS(
        model=os.environ.get("GEMINI_TTS_MODEL", "gemini-2.5-flash-preview-tts"),
        voice_name=os.environ.get("GEMINI_TTS_VOICE", "Kore"),
    )


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
