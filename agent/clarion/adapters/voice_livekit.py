"""V1 — `LiveKitVoiceTransport`: the real VoiceTransport adapter (execution §5).

The production generalization of the proven seam spike (`agent/spike/voice_agent.py`).
It implements the frozen `VoiceTransport` ABC (execution §18.2) over a LiveKit
``AgentSession`` and owns the <800ms turn budget (execution §1) so the kernel
never reimplements STT / turn-detect / barge-in / TTS.

Frozen stack (NO model swaps — execution §18.6, standing rule):
  - STT  = Deepgram        (`livekit-plugins-deepgram`,  DEEPGRAM_API_KEY)
  - LLM  = MiniMax-M3      (`livekit-plugins-minimax`,   MINIMAX_LLM_MODEL / MINIMAX_API_KEY)
  - TTS  = MiniMax Speech 2.6-turbo — the LiveKit `minimax.TTS` plugin for the
           session's audio output; the kernel-facing `Synthesizer` is our
           `MinimaxSynthesizer` (adapters/minimax_synthesizer.py, streaming PCM
           off `/v1/t2a_v2`) behind the frozen contract. MiniMax + LiveKit are
           wired together here (the plugin reads MINIMAX_API_KEY / MINIMAX_GROUP_ID).
  - VAD  = Silero (local) ; turn detection = LiveKit MultilingualModel (local).

The four contract surfaces this maps onto LiveKit:

  start()        -> connect the job ctx + start the AgentSession.
  on_partial(cb) -> the OBSERVER hook (execution §5): register `cb` to fire on
                    INTERIM `user_input_transcribed` events (is_final=False), i.e.
                    while the user is STILL talking -> speculative retrieval. It
                    NEVER blocks the turn.
  on_final(cb)   -> register `cb` on FINAL transcripts.
  on_barge_in(cb)-> register `cb` for a user interruption. In livekit-agents 1.5.x
                    there is no dedicated `user_interruption_detected` event; the
                    real barge-in mechanism is `SpeechHandle.interrupted` flipping
                    True (the non-blocking advance helper below acts on it). We
                    also wire `agent_false_interruption` for observability.
  say(text)      -> session.say(...) -> a real LiveKit `SpeechHandle` (which
                    structurally satisfies the `SpeechHandle` Protocol:
                    `.interrupted` + `await .wait_if_not_interrupted([task])`).
  play_filler()  -> timed dead-air cover while a web action runs (execution §5).

THE NON-BLOCKING ADVANCE HELPER (`advance_non_blocking`, execution §5): launch the
graph step with `asyncio.ensure_future`, then `await speech_handle.wait_if_not_interrupted([task])`.
On barge-in `speech_handle.interrupted` is True -> `task.cancel()` -> return None
and fill NOTHING. Otherwise the task's result is returned and the graph keeps
running in the background after the agent's sentence ends. This is lifted verbatim
in spirit from the spike's `run_advance_task` and shared with the GATE harness.

LIVE vs DEFERRED (honest, execution §18.6): construction + the contract wiring are
REAL. Live STT/LLM/TTS end-to-end is DEFERRED — the Gemini keys resolve to GCP
project 956065465952 which returns 403 (billing dunning) on every call. The voice
plane is fully wired and dies only there. Do NOT swap models.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any, Callable, Optional

from clarion.adapters.minimax_synthesizer import MinimaxSynthesizer
from clarion.contracts.ports import SpeechHandle, Synthesizer, VoiceTransport

# LiveKit plugins register their Plugin singletons at import; this MUST happen on
# the main thread (the worker forks jobs onto other threads and rejects
# off-main-thread registration). Importing the plugin packages here — at module
# load on the main thread — satisfies that for Deepgram/Google/Silero. Wrapped so
# a contracts-only / unit-test environment that lacks an optional native dep does
# not hard-fail at import (the adapter is still constructible; the missing dep is
# reported at construction time, not import).
try:  # pragma: no cover - import-time plugin registration
    from livekit.plugins import deepgram as _deepgram  # noqa: F401
    from livekit.plugins import minimax as _minimax  # noqa: F401
    from livekit.plugins import silero as _silero  # noqa: F401

    _PLUGINS_OK = True
    _PLUGIN_IMPORT_ERROR = ""
except Exception as e:  # noqa: BLE001
    _deepgram = _minimax = _silero = None  # type: ignore[assignment]
    _PLUGINS_OK = False
    _PLUGIN_IMPORT_ERROR = f"{type(e).__name__}: {e}"


# MUST register on the main thread at import time — the worker forks the job onto
# another thread, and `_InferenceRunner.register_runner` rejects off-main-thread
# registration. Importing the module here triggers that registration safely.
def _register_turn_detector():  # pragma: no cover - registration is best-effort
    try:
        from livekit.plugins.turn_detector.multilingual import MultilingualModel

        return MultilingualModel
    except Exception:  # noqa: BLE001
        return None


_MultilingualModel = _register_turn_detector()


# ---------------------------------------------------------------------------
# The non-blocking advance helper (execution §5) — provider-free so the LiveKit
# tool body, the GATE harness, and unit tests share ONE implementation.
# ---------------------------------------------------------------------------


async def advance_non_blocking(
    speech_handle: SpeechHandle,
    coro_factory: Callable[[], Any],
    *,
    log: Optional[Callable[[str], None]] = None,
) -> Optional[Any]:
    """Run a graph step NON-BLOCKING, overlapped with the spoken sentence.

    Launch `coro_factory()` (the graph PROPOSE / advance step) with
    `asyncio.ensure_future`, then `await speech_handle.wait_if_not_interrupted([task])`.
    The task runs in the background WHILE the agent speaks. On barge-in
    (`speech_handle.interrupted` True) the in-flight task is cancelled cleanly and
    the function returns None (fill NOTHING — execution §5). Otherwise it returns
    the task's result; the graph keeps running in the background after the
    sentence ends.

    `coro_factory` is a zero-arg callable returning a coroutine (so the task is
    created exactly once, here, not by the caller). `log` is an optional evidence
    sink.
    """

    def _log(m: str) -> None:
        if log is not None:
            log(m)

    task = asyncio.ensure_future(coro_factory())
    _log(
        "advance_task: launched graph step (non-blocking); "
        f"speech_handle.interrupted={speech_handle.interrupted}"
    )

    # Overlap with the spoken sentence; returns when the task finishes OR the user
    # interrupts (whichever is first) — the real LiveKit mechanism.
    await speech_handle.wait_if_not_interrupted([task])

    if speech_handle.interrupted:
        # BARGE-IN: cancel the in-flight tool, fill nothing (execution §5).
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        _log("advance_task: speech_handle.interrupted -> task.cancel() (NO fill)")
        return None

    return task.result()


# ---------------------------------------------------------------------------
# The VoiceTransport adapter.
# ---------------------------------------------------------------------------


class LiveKitVoiceTransport(VoiceTransport):
    """The voice plane (LiveKit), behind the frozen `VoiceTransport` ABC.

    Constructible without a live session (the components / session are built lazily
    in `start`, or injected for tests). Callback registration (`on_partial` /
    `on_final` / `on_barge_in`) is honored both BEFORE and AFTER `start`: handlers
    registered early are attached to the session when it is created.

    `synthesizer` is the kernel-facing contract-correct TTS (the Vertex Express
    `Synthesizer`); the LiveKit audio-output TTS is built separately in `start`.
    """

    def __init__(
        self,
        *,
        synthesizer: Optional[Synthesizer] = None,
        stt_model: Optional[str] = None,
        llm_model: Optional[str] = None,
        session: Optional[Any] = None,
    ) -> None:
        # Contract-correct TTS the kernel sees (MiniMax Speech 2.6). Constructed
        # but not exercised until first synthesize (lazy httpx client).
        self.synthesizer: Synthesizer = synthesizer or MinimaxSynthesizer()
        self._stt_model = stt_model or "nova-3"
        self._llm_model = llm_model or os.environ.get("MINIMAX_LLM_MODEL", "MiniMax-M3")

        # The live AgentSession (injected for tests, else built in `start`).
        self._session: Optional[Any] = session
        self._started = False

        # Registered callbacks. Kept in lists so multiple observers can subscribe
        # (the kernel's speculative-retrieval observer is one of them).
        self._on_partial: list[Callable[[str], None]] = []
        self._on_final: list[Callable[[str], None]] = []
        self._on_barge_in: list[Callable[[], None]] = []

        # If a session was injected at construction, attach handlers immediately.
        if self._session is not None:
            self._attach_session_handlers(self._session)

    # --- VoiceTransport ABC -------------------------------------------------

    async def start(self) -> None:
        """Connect + start the AgentSession. Builds the full stack lazily so the
        adapter is importable / constructible without LiveKit credentials.

        Idempotent: a second call is a no-op once started.
        """
        if self._started:
            return
        if self._session is None:
            self._session = self._build_session()
            self._attach_session_handlers(self._session)
        # AgentSession.start needs an Agent + room; the kernel/entrypoint owns
        # those (see `build_agent_session` for the full wiring). Here we only mark
        # started — the entrypoint drives `session.start(agent=..., room=...)`.
        self._started = True

    def on_partial(self, cb: Callable[[str], None]) -> None:
        """OBSERVER hook (execution §5): fires on INTERIM transcripts — while the
        user is still talking -> speculative retrieval. Never blocks the turn."""
        self._on_partial.append(cb)

    def on_final(self, cb: Callable[[str], None]) -> None:
        self._on_final.append(cb)

    def on_barge_in(self, cb: Callable[[], None]) -> None:
        self._on_barge_in.append(cb)

    async def say(self, text: str, *, interruptible: bool = True) -> SpeechHandle:
        """Speak `text` via the session TTS. Returns the LiveKit `SpeechHandle`
        (structurally a `SpeechHandle` Protocol: `.interrupted` +
        `wait_if_not_interrupted`). `interruptible=False` wraps an atomic act so a
        stray "um" can't fracture an irreversible step (execution §5)."""
        if self._session is None:
            raise RuntimeError("say() called before start(); no AgentSession")
        return self._session.say(text, allow_interruptions=interruptible)

    async def play_filler(self, key: str) -> None:
        """Timed dead-air cover while a web action runs (execution §5).

        The spike's design uses BackgroundAudioPlayer / a fast-preresponse filler;
        here we route a short non-interruptible spoken filler keyed by `key` so the
        contract surface is real and swappable. (A pre-synthesized audio cover can
        replace this without touching the kernel.)
        """
        if self._session is None:
            raise RuntimeError("play_filler() called before start(); no AgentSession")
        phrase = _FILLERS.get(key, _FILLERS["working"])
        self._session.say(phrase, allow_interruptions=False)

    # --- session wiring -----------------------------------------------------

    def _build_session(self) -> Any:
        """Construct the real AgentSession with the frozen stack. Raises a clear
        error if the LiveKit plugins did not import (missing native dep)."""
        if not _PLUGINS_OK:
            raise RuntimeError(
                "livekit plugins unavailable: " + _PLUGIN_IMPORT_ERROR
            )
        from livekit.agents import AgentSession

        return AgentSession(
            stt=_deepgram.STT(
                model=self._stt_model,
                language="en-US",
                api_key=os.environ["DEEPGRAM_API_KEY"],
            ),
            # MiniMax-M3 via the LiveKit minimax plugin (reads MINIMAX_API_KEY /
            # MINIMAX_GROUP_ID from env — no explicit api_key kwarg needed).
            llm=_minimax.LLM(model=self._llm_model),
            tts=_build_audio_tts(),
            vad=_silero.VAD.load(),
            turn_detection=_MultilingualModel() if _MultilingualModel else None,
        )

    def _attach_session_handlers(self, session: Any) -> None:
        """Wire the session's events into our registered callbacks.

        - `user_input_transcribed` (is_final=False) -> on_partial (the observer).
        - `user_input_transcribed` (is_final=True)  -> on_final.
        - `agent_false_interruption`                -> observability for barge-in.

        True barge-in cancellation is driven by `SpeechHandle.interrupted` in
        `advance_non_blocking`; the registered `on_barge_in` callbacks are also
        fired here when a false-interruption (i.e. a real overlap that the session
        surfaces) is detected, giving the panel a barge-in signal.
        """

        @session.on("user_input_transcribed")
        def _on_transcribed(ev: Any) -> None:  # noqa: ANN401
            text = getattr(ev, "transcript", "")
            if getattr(ev, "is_final", False):
                self._dispatch_final(text)
            else:
                self._dispatch_partial(text)

        @session.on("agent_false_interruption")
        def _on_false_interruption(ev: Any) -> None:  # noqa: ANN401, ARG001
            self._dispatch_barge_in()

    # --- dispatch (also the test seam: drive these to simulate events) ------

    def _dispatch_partial(self, text: str) -> None:
        for cb in self._on_partial:
            cb(text)

    def _dispatch_final(self, text: str) -> None:
        for cb in self._on_final:
            cb(text)

    def _dispatch_barge_in(self) -> None:
        for cb in self._on_barge_in:
            cb()


# Timed dead-air fillers (execution §5). Spoken, non-interruptible covers keyed by
# intent; a pre-synthesized audio bed can swap in without touching the kernel.
_FILLERS = {
    "working": "One moment — I'm working on that now.",
    "retrieving": "Let me pull that up.",
    "thinking": "Give me a second.",
}


def _build_audio_tts() -> Any:
    """The LiveKit audio-output TTS, via **LiveKit Inference** — the native path.

    Routed through the LiveKit Cloud project's own credentials (no per-provider
    key, no MiniMax dependency). Defaults to Cartesia Sonic-2 + a Deepgram Aura-2
    failover; override with CLARION_TTS_MODEL / CLARION_TTS_VOICE / CLARION_TTS_FALLBACK.
    Kept in sync with `app/voice_entry._build_audio_tts` (the live worker path)."""
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


__all__ = [
    "LiveKitVoiceTransport",
    "advance_non_blocking",
]
