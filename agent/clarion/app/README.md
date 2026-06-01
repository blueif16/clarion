# `clarion/app` — I1 integration capstone

The runnable system that wires the FROZEN contracts + Wave-1 adapters + the ST1
stage graph + the instrument into the **live hero flow** on the demo site
(execution §1, §5, §6, §7 I1). This package OWNS only `clarion/app/` and imports
the kernel / stages / actuator / adapters / instrument **read-only** — it never
modifies the frozen contracts or the Wave-1 logic.

## Files

| File | What it is |
|---|---|
| `runtime.py` | `HeroRuntime` — assembles the ST1 stage graph + two retrievers + `PlaywrightActuator` + a `PanelPublisher`. **Two kinds of grounded fact, kept distinct:** `retriever` = `TimedRetriever(HeroRetriever)` for PAGE facts (amount/payee/due/confirmation, each with a `source_node_id`, mirroring the live AXTree); `kb_retriever` = the **LIVE Moss** retriever (`TimedRetriever(MossRetriever)` over the prebuilt `clarion-kb` index) for KB facts (late-fee / autopay policy). `select_kb_retriever` is the selector: LIVE Moss by default, `CachedRetriever` (offline replay of a recorded real Moss query) under `CLARION_DEMO_MODE=1`. `PanelPublisher` maps `ClarionState` → `PanelState` via `instrument.to_panel_state` and can override `retrieval_ms` with the Moss IN-MEMORY `last_runtime_ms` for the KB beat. |
| `kb_beat.py` | I2 — the **KB-retrieval beat** (§6/§8 latency-meter + negative-verification): `MossKBBeat.from_live` queries live Moss (the sub-ms in-memory `last_runtime_ms`, NOT the wall-clock incl. the Gemini embed RPC); `from_cache` replays the recorded result offline. `build_negative_verification` asserts "no late fee currently applied [verified: not present]" ONLY when grounded on BOTH sides (KB late-fee policy exists + the page shows no fee). `ensure_kb_index` ingests the Northwind policy ONCE into `clarion-kb` and REUSES it (never re-ingests per run). |
| `voice_entry.py` | The LiveKit worker entrypoint: V1 `LiveKitVoiceTransport` stack (live Deepgram STT + Gemini LLM + **AI-Studio** Gemini TTS + Silero VAD + MultilingualModel turn detection) + the `advance_task` / `confirm_consent` `@function_tool`s driving the **ST1 stage graph** non-blocking (the proven S1 seam), surfacing each `ConsentRequest` as a spoken readback and HARD-STOPPING at the irreversible PAY in fast mode. |
| `hero_harness.py` | Drives the **full hero run** on the live demo site for verification (the multi-stage generalization of `spike/gate_harness.py`): AUTH+RESCUE → LOCATE (+ the **Moss KB beat**) → FILL → REVIEW → ⟨PAY⟩ consent hard-stop → CONFIRM, publishing `PanelState` after every beat. |
| `record_fixture.py` | POL1 — the RECORD pass. Runs the REAL `PlaywrightActuator` through the hero flow once against the live site and serializes each stage's merged `SelectorMap` (+ the side-channel DOM reads) to `fixtures/hero_selectormaps.json`. The only demo-mode step that touches a browser. |
| `record_moss_fixture.py` | I2 — records ONE real Moss KB query (over the live `clarion-kb`) into `fixtures/hero_moss_kb.json` so `CLARION_DEMO_MODE=1` replays the real grounded facts + the real in-memory number OFFLINE. |
| `demo_mode.py` | POL1 — `CachedActuator(Actuator)`: REPLAYS the recorded fixture (no browser, no network). Selected by `CLARION_DEMO_MODE=1`. Only PERCEPTION is cached; the kernel/stages/consent/policy still run for real. |

## The TTS reconcile (`adapters/tts_vertex.py`)

`VertexExpressSynthesizer` now has **two modes, one adapter**:

- **AI Studio (DEFAULT)** — `genai.Client(api_key=GOOGLE_API_KEY)` (the SDK
  defaults to AI Studio / Gemini Developer API when `vertexai` is unset). The
  `AIzaSy*` key in `agent/.env` is **LIVE** for both the LLM and the TTS preview
  model. This is the path the kernel / voice plane / hero harness use.
- **Vertex Express (flag)** — `genai.Client(vertexai=True, api_key="AQ.*")`, behind
  `mode="vertex"` (or `TTS_MODE=vertex`). Kept for the event in case AI Studio's
  free-tier TTS cap (~100 req/day) is exhausted. The `AQ.*` key is **currently
  billing-blocked** (403 dunning on project 956065465952), so it is NOT the
  default.

Model + voice come from env (`GEMINI_TTS_MODEL` = `gemini-3.1-flash-tts-preview`,
`GEMINI_TTS_VOICE` = `Kore`). **No model swaps** (standing rule).

## Running

The agent venv is `agent/.venv` (all deps present). Load `agent/.env` (the runners
do this for you via `python-dotenv`).

```bash
# 1. Serve the demo site on a known port (used by the harness + voice entry):
cd web/demo-site && npm run dev -- --port 8770    # → http://localhost:8770/

# 2. The FULL hero run (headless, prints PanelState JSON per beat):
cd agent
DEMO_SITE_URL=http://localhost:8770/ .venv/bin/python -m clarion.app.hero_harness

# 3. The LiveKit voice worker (live STT/LLM/TTS):
.venv/bin/python -m clarion.app.voice_entry console   # text/voice console
.venv/bin/python -m clarion.app.voice_entry dev       # connect to a LiveKit room

# 4. The full suite (must stay 70 passing):
.venv/bin/python -m pytest clarion
```

The U1 panel reflects the run live in `?live=1` mode: the publisher sends
`room.local_participant.set_attributes({"panel_state": <PanelState JSON>})`, which
`web/panel`'s `ClarionPanel` subscribes to via `RoomEvent.ParticipantAttributesChanged`.

## LIVE vs SIMULATED (honest, exactly like S1)

- **LIVE**: Playwright/CDP perception (merged AXTree + `PaintOrderRemover` that hides
  the upsell-occluded form), native-setter fills + coordinate clicks + CDP
  read-back; the RESCUE detection on the REAL unlabeled password input; the
  `TimedRetriever` grounding; the REAL K1 kernel / ST1 stage-graph LangGraph consent
  gate at PAY (`interrupt` / `Command(resume=)` / `InMemorySaver`) proving the
  no-act-without-yes hard-stop + completion on consent; `to_panel_state` → PanelState
  JSON publish; **live Gemini TTS** (audio bytes via the reconciled AI-Studio
  adapter) and **live Gemini LLM** (gemini-3.5-flash) on the `GOOGLE_API_KEY`.
- **SIMULATED**: there is no live microphone in this headless env, so the user's
  "yes" at the consent gate is injected programmatically — the MECHANISM exercised
  (LiveKit `SpeechHandle` + LangGraph interrupt/resume) is the real one. The
  page-level choreography between stages (login submit, navigate, dismiss the modal)
  is driven directly through the actuator; the generic kernel `propose` fills a
  single textbox, so the multi-field / navigation steps are the page-aware planner's
  job (a model planner drops into `stages.planner.plan_goal` later — the seam is
  real). The full SPOKEN round-trip runs via LiveKit `console` mode.

## Demo mode (`CLARION_DEMO_MODE`) — judge-proof offline fallback (execution §9)

Honest insurance so the FULL hero run completes even if the network / LiveKit /
Gemini / the demo site is DOWN, or the live AXTree drifts. **Only PERCEPTION is
served from a recorded fixture** — the K1 kernel, the ST1 stage graph, the consent
gate (`interrupt()` / `Command(resume=)`), and the two-clause policy still execute
for real. It is NOT a mock of the outcome: the PAY consent HARD-STOP fires from the
cached submit button exactly as it does live, because the kernel forms the
irreversible proposal from the perceived tree (just replayed). We cache what the
agent *sees*, never what it *decides*.

**Record once (demo site UP):**

```bash
# 1. Serve the demo site:
cd web/demo-site && npm run dev -- --port 8770

# 2. Capture the fixture with the REAL PlaywrightActuator:
cd agent
DEMO_SITE_URL=http://localhost:8770/ .venv/bin/python -m clarion.app.record_fixture
#   → writes clarion/app/fixtures/hero_selectormaps.json
#     stage keys: login, account, pay_upsell, pay_form, pay_filled, pay_submitted
#     side-channel reads: nw_auth, pay_form_values, confirmation
```

**Then run offline (demo site DOWN — the fallback):**

```bash
cd agent
CLARION_DEMO_MODE=1 .venv/bin/python -m clarion.app.hero_harness
#   → CachedActuator replays the fixture; reaches
#     "HERO RUN: GREEN — all six stages pass" with no browser/network.
```

**Cached vs real in demo mode:**

| Cached (replayed from the fixture) | Real (executes live, in-process) |
|---|---|
| `perceive()` — the merged numbered AXTree per page-state | K1 kernel loop (GROUND→VERIFY→PROPOSE→CONSENT→ACT→CONFIRM) |
| `act()` advances a deterministic state machine + logs what it WOULD do | ST1 stage graph + done-predicates + negative checks |
| `read_value()` — recorded field read-backs | The consent gate `interrupt()` + `Command(resume=)` + idempotency once-flag |
| `_page` side-channel reads (`nw_auth`, `conf-num`, `.confirm-banner`) | The two-clause policy (`assert_grounded` / `assert_consented`) |
| | RESCUE detection on the cached unlabeled textbox |
| | `TimedRetriever` grounding + the latency meter + PanelState publish |

The autonomous/live `PlaywrightActuator` path stays the **default** (no flag). With
the site down, the no-flag run errors at `PlaywrightActuator.create` (real
connection failure) while the `CLARION_DEMO_MODE=1` run goes GREEN — proof the demo
is genuinely the fallback, not the live path leaking through.
