"""V1 — Gemini TTS via the `google-genai` SDK, behind the `Synthesizer` contract.

This is the **single source of truth for the adapter layer's TTS** (execution
§18.6 seam discovery). It is the production generalization of the proven spike
(`agent/spike/tts_vertex.py`).

NOTE (R2 / Synthesizer port): Gemini TTS stands in for the Minimax synthesizer
for now. The `Synthesizer` ABC is the swap seam — **later we can switch to
Minimax** by dropping a `MinimaxSynthesizer(Synthesizer)` beside this and
rewiring the adapter; nothing in the kernel/voice plane changes.

TWO modes, ONE adapter (I1 reconcile, 2026-05-31):
  - **AI STUDIO (DEFAULT)** — ``genai.Client(api_key=GOOGLE_API_KEY)`` (the SDK
    defaults to AI Studio / Gemini Developer API when ``vertexai`` is unset). The
    ``AIzaSy*`` key in ``agent/.env`` is LIVE for both the LLM and the TTS preview
    model (verified: 209,280 audio bytes returned, voice Kore). This is the path
    the kernel / voice plane / hero harness use.
  - **VERTEX EXPRESS (flag)** — ``genai.Client(vertexai=True, api_key="AQ.*")``,
    project/location stay None. Kept behind ``mode="vertex"`` (or
    ``TTS_MODE=vertex``) for the event in case AI Studio's free-tier TTS cap
    (~100 req/day) is exhausted. The ``AQ.*`` key is CURRENTLY billing-blocked
    (403 dunning on project 956065465952), so it is NOT the default.

WHY a `google-genai` SDK adapter and not the LiveKit `google.beta.GeminiTTS`
plugin — execution §18.6: the LiveKit plugin's ``vertexai=True`` branch forces the
API key to None and requires a GCP project / ADC, so it cannot consume the AQ.*
Vertex *Express* key. The ports design pays off: TTS drops to the SDK directly,
behind the frozen `Synthesizer` ABC.

`synthesize(text) -> AsyncIterator[bytes]` streams the PCM audio the model returns
(``audio/l16; rate=24000; channels=1``). NEVER swaps the model — model + voice
come straight from the env (`GEMINI_TTS_MODEL`, `GEMINI_TTS_VOICE`), per the
standing "do not swap models to fix latency" rule.
"""

from __future__ import annotations

import asyncio
import os
from typing import AsyncIterator, Literal

from clarion.contracts.ports import Synthesizer

_DEFAULT_MODEL = "gemini-3.1-flash-tts-preview"
_DEFAULT_VOICE = "Kore"

Mode = Literal["ai_studio", "vertex"]


class VertexExpressSynthesizer(Synthesizer):
    """Gemini TTS behind `Synthesizer`. Defaults to **AI Studio mode** (the live
    ``GOOGLE_API_KEY``); ``mode="vertex"`` keeps the Express path for the event.

    The `google-genai` SDK client is constructed lazily on first ``synthesize``
    so the adapter is *importable and constructible* in a headless / no-network
    environment (the V1 unit tests construct it without a key present and assert
    no network call is made). Construction validates config only; the SDK client
    + any auth handshake happen at first use.

    Args:
        api_key: override the resolved key. By default AI Studio mode reads
            ``GOOGLE_API_KEY`` (falling back to ``GEMINI_API_KEY``); Vertex mode
            reads ``VERTEX_API_KEY``.
        model:   TTS model (env ``GEMINI_TTS_MODEL``). NEVER swapped.
        voice:   prebuilt voice name (env ``GEMINI_TTS_VOICE``, default Kore).
        mode:    ``"ai_studio"`` (default) or ``"vertex"``. Falls back to the
            ``TTS_MODE`` env var, then ``"ai_studio"``.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        voice: str | None = None,
        mode: Mode | None = None,
    ) -> None:
        # Resolve mode first (env TTS_MODE, else AI Studio default).
        self._mode: Mode = (
            mode or os.environ.get("TTS_MODE", "ai_studio")  # type: ignore[assignment]
        )
        if self._mode not in ("ai_studio", "vertex"):
            self._mode = "ai_studio"

        # Resolve config eagerly (cheap, no I/O) but DEFER the SDK client so the
        # constructor never touches the network or hard-requires a key at import.
        if api_key is not None:
            self._api_key = api_key
        elif self._mode == "vertex":
            self._api_key = os.environ.get("VERTEX_API_KEY")
        else:  # ai_studio
            self._api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get(
                "GEMINI_API_KEY"
            )
        self._model = model or os.environ.get("GEMINI_TTS_MODEL", _DEFAULT_MODEL)
        self._voice = voice or os.environ.get("GEMINI_TTS_VOICE", _DEFAULT_VOICE)
        self._client = None  # built lazily in _ensure_client()

    @property
    def model(self) -> str:
        return self._model

    @property
    def voice(self) -> str:
        return self._voice

    @property
    def mode(self) -> Mode:
        return self._mode

    def _ensure_client(self):
        """Build the genai client on first use (no I/O at import).

        AI Studio: ``genai.Client(api_key=...)`` (vertexai defaults False).
        Vertex Express: ``genai.Client(vertexai=True, api_key="AQ.*")``.
        """
        if self._client is None:
            if not self._api_key:
                key_name = (
                    "VERTEX_API_KEY (AQ.* Vertex Express key)"
                    if self._mode == "vertex"
                    else "GOOGLE_API_KEY (AI Studio key)"
                )
                raise RuntimeError(
                    f"{key_name} is not set; cannot construct the Gemini TTS client."
                )
            # Imported here so the module is importable even if google-genai is
            # absent in a contracts-only environment.
            from google import genai

            if self._mode == "vertex":
                # Express mode: vertexai=True + api_key, NO project / location.
                self._client = genai.Client(vertexai=True, api_key=self._api_key)
            else:
                # AI Studio (default): plain api_key, vertexai unset.
                self._client = genai.Client(api_key=self._api_key)
        return self._client

    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        """text -> audio stream. The (blocking) genai call runs in a worker thread
        so it never blocks the event loop; the returned PCM bytes are yielded."""
        from google.genai import types

        client = self._ensure_client()
        model = self._model
        voice = self._voice

        def _call() -> bytes:
            resp = client.models.generate_content(
                model=model,
                contents=text,
                config=types.GenerateContentConfig(
                    response_modalities=["AUDIO"],
                    speech_config=types.SpeechConfig(
                        voice_config=types.VoiceConfig(
                            prebuilt_voice_config=types.PrebuiltVoiceConfig(
                                voice_name=voice
                            )
                        )
                    ),
                ),
            )
            part = resp.candidates[0].content.parts[0]
            return part.inline_data.data

        audio = await asyncio.to_thread(_call)
        yield audio

    async def probe(self) -> tuple[bool, str]:
        """Liveness probe for evidence collection. Returns ``(ok, detail)`` without
        raising, so callers can report TTS state honestly (e.g. a billing block)."""
        try:
            chunks = [c async for c in self.synthesize("probe")]
            n = sum(len(c) for c in chunks)
            return True, (
                f"{n} audio bytes via {self._model} (voice {self._voice}, "
                f"mode {self._mode})"
            )
        except Exception as e:  # noqa: BLE001
            return False, f"{type(e).__name__}: {str(e).splitlines()[0][:200]}"


__all__ = ["VertexExpressSynthesizer"]
