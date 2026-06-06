"""V1 — unit tests for the LiveKit VoiceTransport adapter (execution §15 V1).

NO LIVE CALLS. Everything is construct-only / mocked:
  1. ABC conformance: `LiveKitVoiceTransport` satisfies the `VoiceTransport` ABC —
     instantiable (no abstractmethod errors), all six methods present.
  2. Callback registration: `on_partial` / `on_final` / `on_barge_in` register and
     FIRE on a SIMULATED `user_input_transcribed` / false-interruption event, via a
     fake AgentSession that mirrors LiveKit's `session.on(event)` decorator. The
     observer hook (`on_partial`) fires ONLY on interim transcripts.
  3. `say()` returns a SpeechHandle-conforming object; `play_filler` is callable.
  4. The non-blocking advance helper: completes on the happy path; cancels the
     in-flight task and returns None on barge-in (mechanism only — no real audio).

Live STT / LLM / TTS is DEFERRED pending the Gemini billing block (execution
§18.6). These tests assert NO network call is made: the synthesizer's SDK client
is never constructed, and `_build_session` is never reached (a fake session is
injected).
"""

from __future__ import annotations

import asyncio

import pytest

from clarion.adapters.minimax_synthesizer import MinimaxSynthesizer
from clarion.adapters.voice_livekit import (
    LiveKitVoiceTransport,
    advance_non_blocking,
)
from clarion.contracts.ports import SpeechHandle, Synthesizer, VoiceTransport


# ---------------------------------------------------------------------------
# Test doubles — a fake AgentSession mirroring LiveKit's surface (no network).
# ---------------------------------------------------------------------------


class _Event:
    """Mimics a livekit `UserInputTranscribedEvent` (the fields V1 reads)."""

    def __init__(self, transcript: str, *, is_final: bool) -> None:
        self.transcript = transcript
        self.is_final = is_final


class FakeSpeechHandle:
    """Structurally satisfies the `SpeechHandle` Protocol. `interrupted` is fixed
    at construction; `wait_if_not_interrupted` awaits the passed tasks unless
    already interrupted (so the helper's happy path actually runs the coroutine)."""

    def __init__(self, *, interrupted: bool = False) -> None:
        self._interrupted = interrupted

    @property
    def interrupted(self) -> bool:
        return self._interrupted

    async def wait_if_not_interrupted(self, tasks: list) -> None:
        if self._interrupted:
            return
        await asyncio.gather(*tasks, return_exceptions=True)


class FakeAgentSession:
    """Mirrors the slice of `livekit.agents.AgentSession` V1 touches:
    `session.on(event)` as a decorator that registers a handler, `emit(event, ev)`
    to fire it (the test seam), and `say(...)` returning a SpeechHandle. No I/O."""

    def __init__(self) -> None:
        self._handlers: dict[str, list] = {}
        self.said: list[tuple[str, bool]] = []

    def on(self, event: str):
        def _register(fn):
            self._handlers.setdefault(event, []).append(fn)
            return fn

        return _register

    def emit(self, event: str, ev) -> None:
        for fn in self._handlers.get(event, []):
            fn(ev)

    def say(self, text: str, *, allow_interruptions: bool = True):
        self.said.append((text, allow_interruptions))
        return FakeSpeechHandle(interrupted=False)


def _real_or_fake_speech_handle(*, interrupted: bool):
    """Prefer a REAL livekit `SpeechHandle` (the spike's proven mechanism); fall
    back to the structural fake if livekit isn't importable in this env."""
    try:
        from livekit.agents.voice.speech_handle import SpeechHandle as LKSpeechHandle

        h = LKSpeechHandle.create()
        if interrupted:
            h.interrupt(force=True)
        return h
    except Exception:  # noqa: BLE001
        return FakeSpeechHandle(interrupted=interrupted)


# ---------------------------------------------------------------------------
# (1) ABC conformance
# ---------------------------------------------------------------------------


def test_transport_satisfies_voice_transport_abc() -> None:
    # Construct with an injected fake session so no LiveKit/network path runs.
    vt = LiveKitVoiceTransport(session=FakeAgentSession())
    assert isinstance(vt, VoiceTransport)
    # All six abstract methods are concretely present (no abstractmethod error
    # was raised on instantiation; double-check the surface).
    for name in ("start", "on_partial", "on_final", "on_barge_in", "say", "play_filler"):
        assert callable(getattr(vt, name)), f"missing VoiceTransport method: {name}"


def test_transport_constructs_without_session_or_creds() -> None:
    # No injected session, no env — must still construct (lazy session build).
    vt = LiveKitVoiceTransport()
    assert isinstance(vt, VoiceTransport)
    # The kernel-facing synthesizer is the MiniMax one, constructed but with NO
    # HTTP client built yet (no network at construction).
    assert isinstance(vt.synthesizer, Synthesizer)
    assert isinstance(vt.synthesizer, MinimaxSynthesizer)
    assert vt.synthesizer._client is None  # lazy — no httpx.AsyncClient(), no network


# ---------------------------------------------------------------------------
# (2) Callback registration fires on SIMULATED session events
# ---------------------------------------------------------------------------


def test_on_partial_fires_on_interim_transcript_only() -> None:
    session = FakeAgentSession()
    vt = LiveKitVoiceTransport(session=session)

    partials: list[str] = []
    finals: list[str] = []
    vt.on_partial(partials.append)  # the OBSERVER hook (speculative retrieval)
    vt.on_final(finals.append)

    # Simulate STT: an interim transcript (user still talking) then a final one.
    session.emit("user_input_transcribed", _Event("pay my elec", is_final=False))
    session.emit("user_input_transcribed", _Event("pay my electric bill", is_final=True))

    # Observer fired on the INTERIM only; final routed to on_final only.
    assert partials == ["pay my elec"]
    assert finals == ["pay my electric bill"]


def test_on_barge_in_fires_on_simulated_interruption() -> None:
    session = FakeAgentSession()
    vt = LiveKitVoiceTransport(session=session)

    barge_ins: list[int] = []
    vt.on_barge_in(lambda: barge_ins.append(1))

    # Simulate a VAD/turn-detector interruption surfacing via the session event.
    session.emit("agent_false_interruption", object())
    session.emit("agent_false_interruption", object())

    assert barge_ins == [1, 1]


def test_handlers_registered_before_start_attach_to_injected_session() -> None:
    # Handlers registered on a transport that already has a session must be live.
    session = FakeAgentSession()
    vt = LiveKitVoiceTransport(session=session)
    seen: list[str] = []
    vt.on_partial(seen.append)
    session.emit("user_input_transcribed", _Event("hello", is_final=False))
    assert seen == ["hello"]


# ---------------------------------------------------------------------------
# (3) say() returns a SpeechHandle-conforming object; play_filler is callable
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_say_returns_speech_handle_conforming_object() -> None:
    session = FakeAgentSession()
    vt = LiveKitVoiceTransport(session=session)

    handle = await vt.say("I found the amount field.", interruptible=True)
    # Structurally satisfies the frozen SpeechHandle Protocol (.interrupted +
    # wait_if_not_interrupted) — the surface the seam relies on.
    assert isinstance(handle, SpeechHandle)
    assert handle.interrupted is False
    assert session.said[-1] == ("I found the amount field.", True)

    # interruptible=False maps to allow_interruptions=False (atomic act).
    await vt.say("Submitting now.", interruptible=False)
    assert session.said[-1] == ("Submitting now.", False)


@pytest.mark.asyncio
async def test_play_filler_is_callable_and_speaks_noninterruptible() -> None:
    session = FakeAgentSession()
    vt = LiveKitVoiceTransport(session=session)

    await vt.play_filler("retrieving")
    assert session.said, "play_filler did not produce a spoken filler"
    text, interruptible = session.said[-1]
    assert interruptible is False  # dead-air cover is non-interruptible
    assert text  # a real phrase

    # Unknown key falls back to a default filler (still callable, never raises).
    await vt.play_filler("nonexistent-key")
    assert session.said[-1][1] is False


# ---------------------------------------------------------------------------
# (4) The non-blocking advance helper (execution §5) — mechanism only, no audio
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_advance_non_blocking_returns_result_on_happy_path() -> None:
    speech = _real_or_fake_speech_handle(interrupted=False)
    ran = asyncio.Event()

    async def graph_step():
        ran.set()
        return "consent: fill the name field. Say yes."

    result = await advance_non_blocking(speech, graph_step)
    assert ran.is_set()  # the background graph step actually ran
    assert result == "consent: fill the name field. Say yes."


@pytest.mark.asyncio
async def test_advance_non_blocking_cancels_and_returns_none_on_barge_in() -> None:
    # A real (or fake) SpeechHandle that is already interrupted -> the helper must
    # cancel the in-flight task and fill NOTHING.
    speech = _real_or_fake_speech_handle(interrupted=True)
    started = asyncio.Event()
    completed = asyncio.Event()

    async def slow_graph_step():
        started.set()
        try:
            await asyncio.sleep(5)  # would outlast the turn; must be cancelled
        except asyncio.CancelledError:
            raise
        completed.set()
        return "should never reach here"

    result = await advance_non_blocking(speech, slow_graph_step)
    assert result is None  # NO fill on barge-in
    assert not completed.is_set()  # the graph step was cancelled mid-flight


# ---------------------------------------------------------------------------
# No-network assertion: the synthesizer never built an SDK client across the run.
# ---------------------------------------------------------------------------


def test_no_network_synthesizer_client_never_built() -> None:
    vt = LiveKitVoiceTransport(session=FakeAgentSession())
    # We never called synthesize(), so the genai client must remain unbuilt.
    assert vt.synthesizer._client is None
