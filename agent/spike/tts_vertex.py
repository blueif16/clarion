"""S1 — Gemini TTS via Vertex AI Express Mode, behind the Synthesizer contract.

WHY this exists (and not the LiveKit google.beta.GeminiTTS plugin):
The LiveKit `livekit-plugins-google` GeminiTTS plugin's `vertexai=True` branch
**forces the API key to None and requires a GCP project / ADC**
(`.venv/.../livekit/plugins/google/beta/gemini_tts.py` line ~119-126:
`gemini_api_key = None  # VertexAI does not require an API key` and a
`default_async()` ADC project lookup). That path CANNOT consume the AQ.* Vertex
*Express* key, which authenticates against Vertex WITHOUT a project/service
account. So — exactly as the spec anticipated — we drop to the `google-genai`
SDK directly, behind the frozen `Synthesizer` contract.

The Express path the SDK documents (`google/genai/_api_client.py` line ~689,
"Handle when to use Vertex AI in express mode (api key)"):

    genai.Client(vertexai=True, api_key="AQ.*")   # project/location stay None

This wrapper implements the `Synthesizer` ABC: `synthesize(text) -> AsyncIterator[bytes]`,
streaming the PCM audio the TTS model returns. It does NOT swap the model — model
+ voice come straight from the env (`GEMINI_TTS_MODEL`, `GEMINI_TTS_VOICE`).
"""

from __future__ import annotations

import asyncio
import os
from typing import AsyncIterator

from google import genai
from google.genai import types

from clarion.contracts.ports import Synthesizer


class VertexExpressSynthesizer(Synthesizer):
    """Gemini TTS via Vertex AI Express Mode (AQ.* key), behind `Synthesizer`."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        voice: str | None = None,
    ) -> None:
        self._api_key = api_key or os.environ["VERTEX_API_KEY"]
        self._model = model or os.environ.get(
            "GEMINI_TTS_MODEL", "gemini-3.1-flash-tts-preview"
        )
        self._voice = voice or os.environ.get("GEMINI_TTS_VOICE", "Kore")
        # Express mode: vertexai=True + api_key, NO project/location.
        self._client = genai.Client(vertexai=True, api_key=self._api_key)

    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        """text -> audio stream. Runs the (blocking) genai call in a thread so it
        never blocks the event loop, then yields the returned PCM bytes."""

        def _call() -> bytes:
            resp = self._client.models.generate_content(
                model=self._model,
                contents=text,
                config=types.GenerateContentConfig(
                    response_modalities=["AUDIO"],
                    speech_config=types.SpeechConfig(
                        voice_config=types.VoiceConfig(
                            prebuilt_voice_config=types.PrebuiltVoiceConfig(
                                voice_name=self._voice
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
        """Liveness probe for the GATE evidence. Returns (ok, detail) without
        raising, so the spike can report TTS state honestly."""
        try:
            chunks = [c async for c in self.synthesize("probe")]
            n = sum(len(c) for c in chunks)
            return True, f"{n} audio bytes via {self._model} (voice {self._voice})"
        except Exception as e:  # noqa: BLE001
            return False, f"{type(e).__name__}: {str(e).splitlines()[0][:200]}"


__all__ = ["VertexExpressSynthesizer"]
