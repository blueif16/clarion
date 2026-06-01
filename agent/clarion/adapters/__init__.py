"""Clarion Wave-1 adapters — the real provider impls behind the frozen ports.

Provider SDK imports (LiveKit, google-genai, Playwright, ...) live ONLY here and
in sibling adapter modules — NEVER in `contracts/` (foundation §6 / execution §18).

V1 owns the voice seam:
  - `tts_vertex.VertexExpressSynthesizer` — the single source of truth for the
    adapter layer's TTS (Gemini TTS via Vertex Express, behind `Synthesizer`).
  - `voice_livekit.LiveKitVoiceTransport` — the `VoiceTransport` impl over a
    LiveKit AgentSession, with the non-blocking advance helper + observer hook.
"""
