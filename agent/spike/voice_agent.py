"""S1 — the LiveKit voice plane wiring (the seam's top edge, execution §5).

Wires the AgentSession with EXACTLY the spec'd stack (no model swaps):
  - STT = Deepgram (`livekit-plugins-deepgram`, DEEPGRAM_API_KEY)
  - LLM = Google Gemini (`livekit-plugins-google`, GEMINI_MODEL, GOOGLE_API_KEY)
  - TTS = Gemini TTS via Vertex Express (our `VertexExpressSynthesizer` behind the
          Synthesizer contract — the LiveKit google.beta plugin can't take the
          AQ.* express key; see tts_vertex.py). For the LiveKit audio-output path
          we ALSO construct the google.beta.GeminiTTS plugin so the wiring is real
          when a project-backed credential is present; the express synthesizer is
          the contract-correct path the kernel sees.
  - VAD = Silero (local), turn detection = LiveKit MultilingualModel (local).

THE SEAM (execution §5): `advance_task` is a NON-BLOCKING `@function_tool`. Its
body (`run_advance_task`) launches the graph step with `asyncio.ensure_future`
then awaits `run_ctx.speech_handle.wait_if_not_interrupted([task])`. On barge-in
`speech_handle.interrupted` is True → `task.cancel()` → return without filling.
The graph keeps running in the background otherwise. The body is factored out so
the headless GATE harness can drive the IDENTICAL code path against a real
`SpeechHandle` without needing the (billing-blocked) LLM to choose the call.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from typing import Optional

from langgraph.types import Command

from clarion.contracts.events import ConsentDecision, ConsentRequest
from spike.graph import build_spike_graph, seed_state

# LiveKit plugins register their Plugin singletons at import; this MUST happen on
# the main thread (the worker forks jobs onto other threads and rejects
# off-main-thread registration). Importing the plugin packages here — at module
# load on the main thread — satisfies that for Deepgram/Google/Silero. Wrapped so
# the headless GATE harness (which imports this module) doesn't hard-fail if a
# plugin's optional native dep is missing.
try:  # pragma: no cover - import-time plugin registration
    from livekit.plugins import deepgram as _deepgram  # noqa: F401
    from livekit.plugins import google as _google  # noqa: F401
    from livekit.plugins import silero as _silero  # noqa: F401

    _PLUGINS_OK = True
except Exception:  # noqa: BLE001
    _PLUGINS_OK = False

# `RunContext` / `function_tool` MUST be importable in module globals: with
# `from __future__ import annotations`, LiveKit resolves the tool's `RunContext`
# annotation via typing.get_type_hints() against THIS module's globals.
from livekit.agents import RunContext, function_tool  # noqa: E402

_AGENT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ---------------------------------------------------------------------------
# Shared task-plane driver — the seam logic, provider-free so the harness and
# the LiveKit tool share ONE implementation.
# ---------------------------------------------------------------------------


@dataclass
class SeamRunner:
    """Owns the compiled graph + thread_id and exposes the two seam steps:
    `propose()` (runs to the consent interrupt, returns the ConsentRequest) and
    `resume(decision)` (delivers the consent decision, runs ACT→CONFIRM).

    Idempotent by construction: `resume` is safe to call twice — the graph's ACT
    once-flag (execution §2.3) prevents a double-fill.
    """

    actuator: object
    thread_id: str = "spike-thread"
    _graph: object = field(default=None, init=False, repr=False)
    _started: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        self._graph = build_spike_graph(self.actuator)  # type: ignore[arg-type]

    @property
    def _config(self) -> dict:
        return {"configurable": {"thread_id": self.thread_id}}

    async def propose(self) -> Optional[ConsentRequest]:
        """Run PERCEIVE→PROPOSE→(pause at)⟨CONSENT⟩. Returns the surfaced
        ConsentRequest the voice plane must speak, or None if no proposal."""
        result = await self._graph.ainvoke(seed_state(), self._config)  # type: ignore[attr-defined]
        self._started = True
        if "__interrupt__" not in result:
            return None
        (intr,) = result["__interrupt__"]
        return ConsentRequest.model_validate(intr.value)

    async def resume(self, decision: ConsentDecision) -> dict:
        """Deliver the consent decision; runs ACT→CONFIRM. Returns final state."""
        return await self._graph.ainvoke(  # type: ignore[attr-defined]
            Command(resume=decision.model_dump()), self._config
        )

    def state(self) -> dict:
        snap = self._graph.get_state(self._config)  # type: ignore[attr-defined]
        return snap.values


# ---------------------------------------------------------------------------
# The non-blocking tool body (execution §5) — shared by LiveKit + harness.
# ---------------------------------------------------------------------------


async def run_advance_task(speech_handle, runner: SeamRunner, log) -> Optional[str]:
    """The seam: launch the graph PROPOSE step non-blocking, overlap it with the
    spoken sentence, and surface the consent utterance — UNLESS the user barges
    in, in which case cancel cleanly and fill NOTHING.

    `speech_handle` is a LiveKit SpeechHandle (real, in both the agent and the
    harness). `log` is a callable for evidence lines.
    """
    # Launch the graph step in the background (non-blocking, execution §5).
    task = asyncio.ensure_future(runner.propose())
    log(f"advance_task: launched graph PROPOSE (non-blocking); "
        f"speech_handle.interrupted={speech_handle.interrupted}")

    # Overlap with the spoken sentence; returns when the task finishes OR the
    # user interrupts (whichever first) — this is the real LiveKit mechanism.
    await speech_handle.wait_if_not_interrupted([task])

    if speech_handle.interrupted:
        # BARGE-IN: cancel the in-flight tool, fill nothing (execution §5).
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        log("advance_task: speech_handle.interrupted -> task.cancel() "
            "(NO fill on barge-in)")
        return None

    consent_req: Optional[ConsentRequest] = task.result()
    if consent_req is None:
        log("advance_task: graph produced no proposal")
        return None
    log(f"advance_task: PROPOSE complete -> surface consent utterance: "
        f"{consent_req.utterance!r}")
    # The string returned here is what the agent speaks / the readback the user
    # answers 'yes' to. (Stored on the runner for the resume step.)
    return consent_req.utterance


# ---------------------------------------------------------------------------
# LiveKit AgentSession wiring (real providers; constructed but only fully
# drivable once the Gemini billing block clears — see README + report).
# ---------------------------------------------------------------------------


def build_components():
    """Construct the real STT/LLM/TTS/VAD/turn-detection components per the
    frozen stack. Returns a dict; raises only on a hard import/auth-config error,
    NOT on the runtime billing block (that surfaces at first model call)."""
    from spike.tts_vertex import VertexExpressSynthesizer

    components = {
        "stt": _deepgram.STT(
            model="nova-3", language="en-US", api_key=os.environ["DEEPGRAM_API_KEY"]
        ),
        "llm": _google.LLM(
            model=os.environ.get("GEMINI_MODEL", "gemini-3.5-flash"),
            api_key=os.environ["GOOGLE_API_KEY"],
        ),
        # Contract-correct TTS: Gemini TTS via Vertex Express, behind Synthesizer.
        "synthesizer": VertexExpressSynthesizer(),
        "vad": _silero.VAD.load(),
    }
    # turn detection = LiveKit MultilingualModel (local). It binds to the worker's
    # inference executor, so it can ONLY be constructed inside a job entrypoint
    # (`get_job_context()`), not in a bare smoke test. Constructed in `entrypoint`.
    return components


def _build_tts():
    """The LiveKit audio-output TTS. google.beta.GeminiTTS cannot take the AQ.*
    express key (its vertexai branch nulls the key and requires a project/ADC),
    so on the express path it activates only when GOOGLE_APPLICATION_CREDENTIALS /
    a project is present. We still construct it so the wiring is genuine; the
    kernel-facing Synthesizer is VertexExpressSynthesizer (tts_vertex.py)."""
    from livekit.plugins import google

    return google.beta.GeminiTTS(
        model=os.environ.get("GEMINI_TTS_MODEL", "gemini-2.5-flash-preview-tts"),
        voice_name=os.environ.get("GEMINI_TTS_VOICE", "Kore"),
    )


def _build_advance_task_tool(runner: SeamRunner):
    """Build the real LiveKit `@function_tool advance_task` bound to `runner`.

    The LLM calls this when the user asks to act ("fill in my name"). It runs the
    graph PROPOSE step NON-BLOCKING (execution §5) and returns the consent
    utterance for the agent to speak; barge-in cancels the in-flight tool. The
    user's subsequent "yes" is handled by `confirm_consent`."""

    @function_tool()
    async def advance_task(context: RunContext, user_intent: str = "") -> str:
        utterance = await run_advance_task(
            context.speech_handle, runner, lambda m: print(f"  [advance_task] {m}")
        )
        if utterance is None:
            return "Cancelled."
        return utterance  # the agent speaks this readback; user answers yes/no

    @function_tool()
    async def confirm_consent(context: RunContext, approved: bool) -> str:
        """Deliver the user's consent decision to the parked graph (execution
        §2.3). Wrapped so a stray 'um' can't fracture the act."""
        decision = ConsentDecision(decision="approve" if approved else "reject")
        # Atomic act: the actual fill must not be interruptible (execution §5).
        with context.disallow_interruptions():
            final = await runner.resume(decision)
        log = final["consent_log"][-1].decision if final.get("consent_log") else "?"
        return f"Done. Consent recorded: {log}."

    return [advance_task, confirm_consent]


def prewarm(proc) -> None:
    """Load the Silero VAD weights into the job process before assignment
    (LiveKit prewarm pattern). The MultilingualModel turn detector registers its
    inference runner at module import on the main thread (see `_register_turn_detector`)."""
    proc.userdata["vad"] = _silero.VAD.load()


# MUST register on the main thread at import time — the worker forks the job onto
# another thread, and `_InferenceRunner.register_runner` rejects off-main-thread
# registration. Importing the module here triggers that registration safely.
def _register_turn_detector():
    try:
        from livekit.plugins.turn_detector.multilingual import (  # noqa: F401
            MultilingualModel,
        )

        return MultilingualModel
    except Exception:  # pragma: no cover - registration is best-effort at import
        return None


_MultilingualModel = _register_turn_detector()


async def entrypoint(ctx) -> None:
    """LiveKit worker entrypoint (execution §5). Builds the real AgentSession with
    Deepgram STT + Gemini LLM + Gemini-TTS + Silero VAD + MultilingualModel turn
    detection, attaches the `advance_task` seam tool over a CDP actuator on the C2
    page, and starts the session. Runnable via console mode (see README)."""
    from livekit.agents import Agent, AgentSession

    from spike.actuator_min import MinActuator

    await ctx.connect()
    comp = build_components()
    # Prefer the prewarmed VAD if present.
    vad = ctx.proc.userdata.get("vad") if hasattr(ctx, "proc") else None
    actuator = await MinActuator.create(
        os.environ.get("SPIKE_TARGET_URL", "http://127.0.0.1:8765/index.html"),
        headless=True,
    )
    runner = SeamRunner(actuator=actuator, thread_id="voice-console")
    tools = _build_advance_task_tool(runner)

    agent = Agent(
        instructions=(
            "You are Clarion, a voice web co-pilot that keeps the user in command. "
            "When the user asks to fill in their name (or 'fill in my name'), call "
            "advance_task. Speak the readback it returns VERBATIM, then wait for the "
            "user to say yes or no. When they answer, call confirm_consent with "
            "approved=true for yes or approved=false for no. Never fill anything "
            "without an explicit yes. Be concise; no emojis or markdown."
        ),
        tools=tools,
    )
    session = AgentSession(
        stt=comp["stt"],
        llm=comp["llm"],
        tts=_build_tts(),
        vad=vad or comp["vad"],
        turn_detection=_MultilingualModel() if _MultilingualModel else None,
    )
    await session.start(agent=agent, room=ctx.room)
    await session.generate_reply(
        instructions="Greet the user briefly and tell them you can fill in their name."
    )


def main() -> None:
    """Console/dev runner. `python -m spike.voice_agent console` for the LiveKit
    text/voice console (needs a working LLM — see report re: Gemini billing block);
    the GATE evidence runs via spike.gate_harness regardless."""
    from dotenv import load_dotenv

    load_dotenv(os.path.join(_AGENT_ROOT, ".env"))
    from livekit.agents import WorkerOptions, cli

    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint, prewarm_fnc=prewarm))


__all__ = [
    "SeamRunner",
    "run_advance_task",
    "build_components",
    "entrypoint",
    "main",
]


if __name__ == "__main__":
    main()
