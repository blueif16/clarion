# Clarion — Project Instructions

**Clarion** is a voice co-pilot that lets blind/low-vision people finish private, high-stakes
web tasks themselves: it finds the thing, reads back exactly what's on the page (and says when
it *can't* find something instead of guessing), and keeps the human in command at every
consequential step. Built for the YC Conversational AI Hackathon (June 6–7 2026).

Full spec: `docs/foundation.md` (product, LOCKED) · `docs/execution.md` (build spec + Part III status).
**▶ READ FIRST each session: `docs/clarion-status.md`** — LIVE progress: what's real vs hardcoded, what's left, what to fix/test. Keep it current (edit it in the same commit as the change). Next-session kickoff prompt: `docs/clarion-handoff-prompt.md`.

## The invariant (this is the kernel — do not erode it)
> **No fact without a source. No action without a yes.**
- **Epistemic:** never *speak* a fact not just retrieved, incl. negatives ("no late fee here"). A `Fact` with `source_node_id = None` is ungrounded and MUST NOT be spoken.
- **Agentic:** never commit an irreversible side-effect without an explicit per-step "yes."

## Summary rules (detail below)
- ALWAYS keep `contracts/` and `kernel/` free of provider SDKs — providers live only in Wave-1 adapters.
- NEVER swap models to fix latency — pipeline/stream/parallelize first; model choices are fixed.
- ALWAYS git with `git -C /Users/tk/Desktop/conv-agent` + explicit pathspecs (repo is home-rooted).
- NEVER use the words "assistant"/"helper"/"assist" in any copy, UI, or voice line.
- Work on feature branches; commit per logical unit (conventional commits); don't push unless asked.

## Architecture — two planes + an actuator, wired by events
- **Voice plane** (LiveKit): STT · turn-detect · barge-in · TTS · filler. Owns the <800ms turn budget.
- **Task plane** (LangGraph kernel): `GROUND ▶ VERIFY ▶ PROPOSE ▶ ⟨CONSENT⟩ ▶ ACT ▶ CONFIRM`. Checkpointer = durable goal-state; `interrupt()` = consent.
- **Actuator** (Playwright/CDP): merged numbered AXTree → `selector_map` → act → re-perceive.
- The planes talk via **events, not nested loops**: voice calls `advance_task()` non-blocking; the task plane surfaces consent via `interrupt()`. Everything is behind a port (`VoiceTransport`/`Retriever`/`Synthesizer`/`Actuator`/`Ingest`/`Memory`) — the kernel imports zero provider SDKs.

## Repo layout (directory ownership = collision-free)
```
agent/clarion/contracts/  ports.py · state.py · events.py   ← FROZEN; pure pydantic/abc/typing
agent/clarion/fakes/      in-memory impl of every port
agent/clarion/kernel/     graph.py · policy.py              ← 6-node loop, 2-clause policy, 2 modes
agent/clarion/actuator/   merged-AXTree perception + act + diff
agent/clarion/stages/     planner + per-stage nodes + RESCUE cross-cut
agent/clarion/adapters/   voice_livekit.py · tts_vertex.py  ← real providers live here
agent/clarion/retrieval/  Moss + Gemini-embedding stack
agent/clarion/instrument/ latency meter + cold-RAG baseline + to_panel_state
agent/clarion/app/        runtime · hero_harness · voice_entry · demo_mode · kb_beat
web/demo-site/  (hero target) · web/panel/ (six effects) · web/spike-target/ (S1 gate)
docs/persona.md · scripts/copy_lint.py
```

## Stack (versions locked)
- Agent: Python 3.12+, `langgraph 1.2.2` (`interrupt`/`Command` from `langgraph.types`; `InMemorySaver` canonical), `pydantic 2.13.4`, `playwright`, `livekit-agents`.
- Frontend: Next.js 16.2.2 + React 19 (Turbopack), `@livekit/components-react`.
- Providers split by extra: `.[test]` (no network), `.[spike]` (LiveKit+Playwright+genai), `.[retrieval]` (Moss+genai).

## Contract gotchas (these are load-bearing — the §18.6/18.7 freeze decisions)
- `ClarionState.trace` and `consent_log` use `Annotated[list, operator.add]` reducers. Every node must return **only new entries** — returning the full list overwrites the audit log and breaks the idempotency guard. `grounded_facts` stays last-value-wins (nodes manage it explicitly).
- `step: tuple[int,int]` round-trips through the checkpointer as a `list` — **consumers coerce on read** (`tuple(state["step"])`). Don't relax the annotation.
- **Idempotent ACT:** on `Command(resume=)` the interrupted node re-executes from the top. ACT checks the `consent_log` once-flag before side-effecting — never bolt a second guard on elsewhere.
- The checkpointer adapter must allowlist the contract module (`allowed_msgpack_modules`) to keep durability warning-free. Contracts stay pure; this is adapter-side.

## Run it all
```bash
# deterministic regression gate (no network) — 82 passed, 3 deselected
cd agent && pip install -e ".[test]" && python -m pytest clarion
python -m pytest clarion -m live                        # 3 live Moss tests (needs creds)

cd web/demo-site && npm install && npm run dev -- --port 8770   # hero target; login pw: demo

# FULL hero run (live: Playwright + Moss + Gemini)
cd agent && pip install -e ".[spike]" && pip install -e ".[retrieval]"
.venv/bin/playwright install chromium
DEMO_SITE_URL=http://localhost:8770/ .venv/bin/python -m clarion.app.hero_harness
CLARION_DEMO_MODE=1 .venv/bin/python -m clarion.app.hero_harness   # judge-proof offline run
.venv/bin/python -m clarion.app.voice_entry console     # live voice worker (human speaks)
cd web/panel && npm run dev                             # legibility panel (?live=1)
python scripts/copy_lint.py <file>                      # banned-word lint
```
Secrets in `agent/.env` (gitignored; template `agent/.env.example`).

## Provider state (event-day truth)
- **LiveKit** live · **Deepgram** STT live · **Gemini LLM+TTS** live via AI Studio (`gemini-3.5-flash` + `gemini-3.1-flash-tts-preview`/Kore; Vertex-Express `AQ.*` key is billing-blocked, kept behind `TTS_MODE=vertex`).
- **Moss** retrieval live; its embed host + cloud-query are bypassed by Gemini `gemini-embedding-001` custom embeddings. `clarion-kb` index is built + persistent (reused).
- **Minimax** — Gemini TTS stands in (swap seam = the `Synthesizer` ABC).
- Pre-warm the Moss index and fire the embed on partial-STT so the on-stage number is the in-memory ~3ms (the ~2.7s embed RPC must overlap speech).

## Git
- Repo is rooted at `/Users/tk` (home), not at `agent/`. Always scope: `git -C /Users/tk/Desktop/conv-agent status -- <paths>`; never run an unscoped `git status` (it walks the entire home dir).
- Feature branches only; conventional commits (`feat:`/`fix:`/`docs:`); push only when asked.
