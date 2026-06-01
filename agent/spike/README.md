# S1 — Seam Spike (the GATE)

De-risks the one novel unknown for Clarion: wiring a **LiveKit voice loop ↔ a
LangGraph task graph ↔ a CDP accessibility-tree actuator**, with a consent
interrupt — proving the round-trip *speak → perceive → propose → consent → act →
confirm* on a single field, with barge-in cancellation and idempotency.

This package OWNS `agent/spike/`. It imports `clarion.contracts` (the frozen C1
artifact) and the real providers (LiveKit, Playwright/CDP, google-genai). It does
**not** modify `contracts/` or any other dir.

## Files

| File | What |
|---|---|
| `actuator_min.py` | Minimal `Actuator` (Playwright + CDP `Accessibility.getFullAXTree`) → numbered `SelectorMap`; native-setter fill; CDP read-back. |
| `graph.py` | LangGraph: PERCEIVE → PROPOSE(deterministic) → `interrupt(ConsentRequest)` → `Command(resume=ConsentDecision)` → ACT(idempotent) → CONFIRM. `InMemorySaver` + `thread_id` + msgpack allowlist (execution §18.6). |
| `tts_vertex.py` | `Synthesizer` impl — Gemini TTS via **Vertex AI Express Mode** (`google-genai`, AQ.* key). The LiveKit `google.beta.GeminiTTS` plugin can't take the express key (see "Known gaps"). |
| `voice_agent.py` | LiveKit `AgentSession` wiring (Deepgram STT + Gemini LLM + Gemini TTS + Silero VAD + MultilingualModel turn detection) + the non-blocking `@function_tool advance_task` seam + a console runner. |
| `gate_harness.py` | The GATE: produces evidence for (a) round-trip, (b) barge-in/cancel, (c) idempotency, driving the REAL seam path. |

## Setup (once)

```bash
cd agent
pip install -e ".[spike]"          # livekit-agents, plugins, playwright, google-genai, dotenv
.venv/bin/playwright install chromium
.venv/bin/python -m livekit.agents download-files   # turn-detector + Silero VAD model files
```

Credentials are read from `agent/.env` via python-dotenv (LIVEKIT_*, DEEPGRAM_API_KEY,
GOOGLE_API_KEY, GEMINI_MODEL, VERTEX_API_KEY, GEMINI_TTS_MODEL/VOICE).

## Run the GATE (the evidence — no live mic required)

```bash
cd agent
# 1. serve the C2 target page
.venv/bin/python -m http.server 8765 --bind 127.0.0.1 --directory ../web/spike-target &
# 2. run the three gate conditions against it
SPIKE_TARGET_URL="http://127.0.0.1:8765/index.html" .venv/bin/python -m spike.gate_harness
```

Expected tail: `GATE: GREEN — all three conditions pass`.

Standalone actuator self-check (perceive → native-setter fill → CDP read-back):

```bash
SPIKE_TARGET_URL="http://127.0.0.1:8765/index.html" .venv/bin/python -m spike.actuator_min
```

## Run the live LiveKit voice agent (console mode)

```bash
cd agent
.venv/bin/python -m http.server 8765 --bind 127.0.0.1 --directory ../web/spike-target &
.venv/bin/python -m spike.voice_agent console          # voice; or `console --text` for text I/O
```

This starts the real worker, connects a room, builds the AgentSession with the
full stack, and registers the `advance_task` seam over a headless CDP actuator on
the C2 page. Say "fill in my name" → the agent calls `advance_task` (non-blocking),
speaks the readback, and on "yes" calls `confirm_consent` → the graph fills the
field. **Requires a working Gemini LLM** (see Known gaps).

## How the seam works (execution §5 / §2.3)

- `advance_task` (in `voice_agent.run_advance_task`) launches the graph PROPOSE
  step with `asyncio.ensure_future(...)`, then `await
  speech_handle.wait_if_not_interrupted([task])`. The graph runs in the background
  *while the agent speaks*. On barge-in, `speech_handle.interrupted` is True →
  `task.cancel()` → returns without filling.
- The actual fill is wrapped in `context.disallow_interruptions()` (atomic act).
- **Idempotency (load-bearing, §2.3):** on `Command(resume=)` the interrupted
  CONSENT node re-executes from the top and ACT runs again. ACT is guarded by a
  `consent_log` approve-check **plus** a once-flag (an `acted_proposal_id` trace
  marker), so a second `resume(approve)` does NOT double-fill.

## Known gaps (real, account-side — not code)

1. **Gemini is billing-blocked.** Both the AI Studio `GOOGLE_API_KEY` (the voice
   LLM) and the AQ.* `VERTEX_API_KEY` (TTS) resolve to GCP project
   `956065465952`, which returns `403 PERMISSION_DENIED — Lightning dunning
   decision is deny` on **every** call (text + TTS, every model). "Dunning" is a
   billing-collections state. The code constructs both paths correctly; the block
   is external. The GATE evidence does not depend on Gemini.
2. **LiveKit `google.beta.GeminiTTS` cannot take the AQ.* express key.** Its
   `vertexai=True` branch nulls the API key and requires a GCP project/ADC
   (`gemini_tts.py` ~L119–126). So TTS goes through `tts_vertex.VertexExpressSynthesizer`
   (`google-genai` SDK, `genai.Client(vertexai=True, api_key="AQ.*")`) behind the
   `Synthesizer` contract — the documented express path.
