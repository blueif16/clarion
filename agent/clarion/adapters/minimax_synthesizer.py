"""``MinimaxSynthesizer`` — the live ``Synthesizer`` (MiniMax Speech 2.6, T2A v2).

The production TTS behind the frozen ``Synthesizer`` ABC, replacing the Gemini
stand-in (``VertexExpressSynthesizer``). It streams PCM audio from MiniMax's
``/v1/t2a_v2`` endpoint with model ``speech-2.6-turbo`` (sub-250ms time-to-first-
audio, built for real-time voice agents — fits Clarion's <800ms turn budget).

Wire (env, NEVER invented):
  - key      ``MINIMAX_API_KEY``                  (Bearer)
  - group    ``MINIMAX_GROUP_ID``                 (optional ``?GroupId=`` query param)
  - model    ``MINIMAX_TTS_MODEL``   default ``speech-2.6-turbo``
  - voice    ``MINIMAX_TTS_VOICE``   default ``Friendly_Person``
  - host     ``MINIMAX_TTS_HOST``    default ``https://api.minimax.io``

``synthesize(text) -> AsyncIterator[bytes]`` requests ``stream=True`` and yields
each PCM chunk as it arrives: MiniMax delivers SSE ``data:`` frames whose
``data.audio`` is hex-encoded audio; we decode + yield per frame. Output is raw
PCM (``audio_setting.format='pcm'``) at 24kHz mono to match the kernel's existing
``audio/l16; rate=24000`` contract — same byte shape the voice plane already
consumed from the Gemini synthesizer, so nothing downstream changes.

The HTTP client (``httpx``) is imported + constructed LAZILY on first
``synthesize`` so the adapter is importable / constructible in a headless /
no-network / no-key environment (the unit tests construct it and assert no client
and no network at construction). NEVER swaps the model — model + voice come
straight from env, per the standing "do not swap models to fix latency" rule.
"""

from __future__ import annotations

import json
import os
from typing import Any, AsyncIterator

from clarion.contracts.ports import Synthesizer

_DEFAULT_MODEL = "speech-2.6-turbo"
_DEFAULT_VOICE = "Friendly_Person"
_DEFAULT_HOST = "https://api.minimax.io"
# Match the existing PCM contract the kernel/voice plane already consumed.
_SAMPLE_RATE = 24000


class MinimaxSynthesizer(Synthesizer):
    """MiniMax Speech 2.6 TTS behind ``Synthesizer``. Streams hex-decoded PCM.

    The ``httpx`` client is built lazily on first ``synthesize`` so the adapter is
    *importable and constructible* with no network / no key (the unit tests
    construct it without a key present and assert no client is built). Construction
    validates config only.

    Args:
        api_key: override the resolved key (env ``MINIMAX_API_KEY``).
        group_id: optional MiniMax group id (env ``MINIMAX_GROUP_ID``) → ``?GroupId=``.
        model:   TTS model (env ``MINIMAX_TTS_MODEL``, default ``speech-2.6-turbo``).
        voice:   voice id (env ``MINIMAX_TTS_VOICE``, default ``Friendly_Person``).
        host:    API host (env ``MINIMAX_TTS_HOST``, default ``https://api.minimax.io``).
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        group_id: str | None = None,
        model: str | None = None,
        voice: str | None = None,
        host: str | None = None,
    ) -> None:
        # Resolve config eagerly (cheap, no I/O) but DEFER the HTTP client so the
        # constructor never touches the network or hard-requires a key at import.
        self._api_key = api_key or os.environ.get("MINIMAX_API_KEY")
        self._group_id = group_id or os.environ.get("MINIMAX_GROUP_ID")
        self._model = model or os.environ.get("MINIMAX_TTS_MODEL", _DEFAULT_MODEL)
        self._voice = voice or os.environ.get("MINIMAX_TTS_VOICE", _DEFAULT_VOICE)
        self._host = (host or os.environ.get("MINIMAX_TTS_HOST", _DEFAULT_HOST)).rstrip("/")
        self._sample_rate = _SAMPLE_RATE
        self._client: Any = None  # httpx.AsyncClient, built lazily in _ensure_client()

    @property
    def model(self) -> str:
        return self._model

    @property
    def voice(self) -> str:
        return self._voice

    def _endpoint(self) -> str:
        url = f"{self._host}/v1/t2a_v2"
        if self._group_id:
            url = f"{url}?GroupId={self._group_id}"
        return url

    def _ensure_client(self):
        """Build the httpx async client on first use (no I/O at import)."""
        if self._client is None:
            if not self._api_key:
                raise RuntimeError(
                    "MINIMAX_API_KEY is not set; cannot construct the MiniMax TTS "
                    "client."
                )
            import httpx

            self._client = httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0))
        return self._client

    def _payload(self, text: str) -> dict[str, Any]:
        return {
            "model": self._model,
            "text": text,
            "stream": True,
            "voice_setting": {
                "voice_id": self._voice,
                "speed": 1.0,
                "vol": 1.0,
                "pitch": 0,
            },
            # Raw PCM @ 24kHz mono → same byte shape as the prior Gemini L16 stream.
            "audio_setting": {
                "sample_rate": self._sample_rate,
                "format": "pcm",
                "channel": 1,
            },
        }

    async def synthesize(self, text: str) -> AsyncIterator[bytes]:
        """text -> PCM stream. Opens an SSE stream against ``/v1/t2a_v2`` and yields
        each frame's hex-decoded audio as it arrives (low TTFA)."""
        client = self._ensure_client()
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        async with client.stream(
            "POST", self._endpoint(), headers=headers, json=self._payload(text)
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                try:
                    frame = json.loads(line[5:])
                except json.JSONDecodeError:
                    continue
                audio_hex = (frame.get("data") or {}).get("audio")
                if audio_hex:
                    yield bytes.fromhex(audio_hex)

    async def aclose(self) -> None:
        """Close the underlying httpx client if it was built."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def probe(self) -> tuple[bool, str]:
        """Liveness probe for evidence collection. Returns ``(ok, detail)`` without
        raising, so callers can report TTS state honestly."""
        try:
            chunks = [c async for c in self.synthesize("probe")]
            n = sum(len(c) for c in chunks)
            return True, f"{n} PCM bytes via {self._model} (voice {self._voice})"
        except Exception as e:  # noqa: BLE001
            return False, f"{type(e).__name__}: {str(e).splitlines()[0][:200]}"


__all__ = ["MinimaxSynthesizer"]
