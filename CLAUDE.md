# Clarion — Project Instructions

**Clarion** is a voice co-pilot that lets blind/low-vision people finish private, high-stakes
web tasks themselves: it finds the thing, reads back exactly what's on the page (and says when
it *can't* find something instead of guessing), and keeps the human in command at every
consequential step. Built for the YC Conversational AI Hackathon (June 6–7 2026).

Full spec: `docs/foundation.md` (product, LOCKED) · `docs/execution.md` (build spec + Part III status).
**▶ READ FIRST each session: `docs/clarion-status.md`** — LIVE progress: what's real vs hardcoded, what's left, what to fix/test. Keep it current (edit it in the same commit as the change). Next-session kickoff prompt: `docs/clarion-handoff-prompt.md`.

## The invariant (this is the kernel — do not erode it)
> **No fact without a source. No action without a yes. No memory without a yes.**
- **Epistemic:** never *speak* a fact not just retrieved, incl. negatives ("no late fee here"). A `Fact` with `source_node_id = None` is ungrounded and MUST NOT be spoken.
- **Agentic:** never commit an irreversible side-effect without an explicit per-step "yes."
- **Memory** (knowledge layer, `CLARION_MEMORY=1`): never persist a user fact/preference/workflow without an explicit "remember this" yes; secrets are never offered (`app/remember.py`). A recalled value re-enters as a HINT to re-ground — `Recall` has no `source_node_id`, so it is structurally unspeakable, never spoken from memory. Spec: `docs/clarion-memory-design.md`.

## Summary rules (detail below)
- ALWAYS keep `contracts/` and `kernel/` free of provider SDKs — providers live only in Wave-1 adapters.
- NEVER swap models to fix latency — pipeline/stream/parallelize first; model choices are fixed.
- NEVER hard-code word/keyword lists (stopwords, name/intent matchers, synonym tables) to classify, rank, or route — derive meaning from the LLM or embeddings behind a port. Lexical token-matching on page/goal text is the de-hardcoding thesis's banned heuristic (it's why `_topk_slice`'s lexical rank must become a semantic `ContextRanker`).
- Knowledge-layer cache is **advisory** (live re-perceive is authority); freshen by **verify-on-use, not TTL**; any site **graph lives beside Moss**, never inside it (Moss is vector-only). Detail in Provider state.
- ALWAYS git with `git -C /Users/tk/Desktop/conv-agent` + explicit pathspecs (repo is home-rooted).
- NEVER use the words "assistant"/"helper"/"assist" in any copy, UI, or voice line.
- Work on feature branches; commit per logical unit (conventional commits); don't push unless asked.

## Architecture — two planes + an actuator, wired by events
- **Voice plane** (LiveKit): STT · turn-detect · barge-in · TTS · filler. Owns the <800ms turn budget.
- **Task plane** (LangGraph kernel): `GROUND ▶ VERIFY ▶ PROPOSE ▶ ⟨CONSENT⟩ ▶ ACT ▶ CONFIRM`. **De-hardcoded**: a generic `Reasoner` LLM plans the goal and decides each grounded step (ZERO site-specific topology — proven on real gov sites); the kernel enforces the two invariants in code. Checkpointer = durable goal-state; `interrupt()` = consent.
- **Actuator** (merged numbered AXTree → `selector_map` → act → re-perceive) over two transports: the extension's `chrome.debugger` relay (product path, the user's real tab — `CLARION_ACTUATOR=extension`) or Playwright/CDP (autonomous proof).
- The planes talk via **events, not nested loops**: voice calls `advance_task()` non-blocking; the task plane surfaces consent via `interrupt()`. Everything is behind a port (`VoiceTransport`/`Reasoner`/`Retriever`/`Synthesizer`/`Actuator`/`Ingest`/`Memory`) — the kernel imports zero provider SDKs.

## Repo layout (directory ownership = collision-free)
```
agent/clarion/contracts/  ports.py · state.py · events.py   ← FROZEN; pure pydantic/abc/typing
agent/clarion/fakes/      in-memory impl of every port
agent/clarion/kernel/     graph.py · policy.py · irreversibility.py · reasoner_guard.py  ← 6-node loop, 2-clause policy
agent/clarion/actuator/   merged-AXTree perception + act + diff
agent/clarion/stages/     planner + generic executor + checks + RESCUE cross-cut
agent/clarion/adapters/   voice_livekit.py · minimax_reasoner.py · minimax_synthesizer.py  ← real providers live here
agent/clarion/retrieval/  moss_client · retriever_moss · memory_moss · ingest_gemini (embed-vector fallback)
agent/clarion/instrument/ latency meter + cold-RAG baseline + to_panel_state
agent/clarion/app/        voice_entry · extension_runtime · gov_proof · remember · site_indexer · runtime
web/extension/  THE PRODUCT — Chrome MV3 (service-worker · offscreen · hud · relay-client)
web/demo-site/ · web/panel/  Next.js aux (NOT test targets) · web/spike-target/
docs/persona.md · scripts/copy_lint.py
```

## Stack (versions locked)
- Agent: Python 3.12+, `langgraph` 1.x (`interrupt`/`Command` from `langgraph.types`; `InMemorySaver` canonical), `pydantic` 2.x, `playwright`, `livekit-agents`.
- Frontend: **Chrome MV3 extension** (`web/extension/`, vanilla JS — service-worker + offscreen audio doc + HUD + relay-client). This is the product UI; `web/demo-site` + `web/panel` are Next.js 16 / React 19 auxiliaries, not the deliverable and not test targets.
- Providers split by extra: `.[test]` (no network), `.[spike]` (LiveKit + MiniMax + Deepgram + **anthropic plugin** for the MiniMax voice LLM + Playwright + genai), `.[retrieval]` (Moss + genai embed-fallback).
- **NEVER `pip install -U livekit-plugins-minimax` — it is PINNED at `1.2.9`.** Its latest (`1.3.0`) pins `livekit-agents==1.2.9` and silently downgrades agents, breaking the `deepgram`/`anthropic`/`turn-detector` 1.5.15 plugins. `1.2.9` has a loose pin and coexists with agents `1.5.15`. It is now **off the live voice path entirely** — the voice LLM uses the `anthropic` plugin and TTS uses **LiveKit Inference** (`inference.TTS`); the plugin survives only as a still-declared dep (an `# noqa: F401` availability import), and the pin stays so it can't drag `livekit-agents` back to `1.2.9`.

## Contract gotchas (these are load-bearing — the §18.6/18.7 freeze decisions)
- `ClarionState.trace` and `consent_log` use `Annotated[list, operator.add]` reducers. Every node must return **only new entries** — returning the full list overwrites the audit log and breaks the idempotency guard. `grounded_facts` stays last-value-wins (nodes manage it explicitly).
- `step: tuple[int,int]` round-trips through the checkpointer as a `list` — **consumers coerce on read** (`tuple(state["step"])`). Don't relax the annotation.
- **Idempotent ACT:** on `Command(resume=)` the interrupted node re-executes from the top. ACT checks the `consent_log` once-flag before side-effecting — never bolt a second guard on elsewhere.
- The checkpointer adapter must allowlist the contract module (`allowed_msgpack_modules`) to keep durability warning-free. Contracts stay pure; this is adapter-side.

## Run it all
```bash
# deterministic regression gate (no network) — 191 passed, 10 deselected
cd agent && pip install -e ".[test]" && python -m pytest clarion
python -m pytest clarion -m live                        # live Moss tests (needs creds)

# live providers: MiniMax brain+voice, Deepgram STT, Playwright, Moss
cd agent && pip install -e ".[spike]" && pip install -e ".[retrieval]"
.venv/bin/playwright install chromium

# autonomous de-hardcoded proof (MiniMax-M3 decides, Playwright + Moss, REAL sites):
.venv/bin/python -m clarion.app.gov_proof

# live-voice product path (the extension on a real tab):
scripts/clarion-up.sh        # logsink+broker+worker + opens Chrome for Testing
scripts/clarion-status.sh    # ports + procs + tail of every log (run this first)
scripts/clarion-down.sh      # stop everything

python scripts/copy_lint.py <file>                      # banned-word lint
```
Secrets in `agent/.env` (gitignored; template `agent/.env.example`).

## Provider state (event-day truth)
- **LiveKit** voice transport live · **Deepgram** STT live (`nova-3`, `smart_format`, `endpointing_ms=300` so halting speech isn't chopped mid-word). **EN-only by design** (`STT_LANGUAGE=en-US`): Deepgram can't code-switch EN+Chinese in one stream — `multi` excludes Chinese, Chinese needs a dedicated `zh-CN`/`zh-HK` model.
- **MiniMax is the brain; LiveKit Inference is the voice (TTS).** **MiniMax-M3** (OpenAI-compatible, `api.minimax.io/v1`) is the kernel `Reasoner` (plan + per-step decide) and — via MiniMax's Anthropic-compatible gateway (LiveKit `anthropic` plugin; native `thinking` blocks dropped from spoken text) — the voice-plane LLM; **MiniMax-M2.7** is the failover (`llm.FallbackAdapter`, so an M3 5xx fails over instead of going silent). **The LiveKit voice-plane TTS is LiveKit Inference** (`inference.TTS`, native — routed through the LiveKit Cloud project's own `LIVEKIT_API_KEY/SECRET`, **no per-provider key**): default **Cartesia Sonic-2** + automatic **Deepgram Aura-2** failover (`inference`'s built-in `fallback=`), knobs `CLARION_TTS_MODEL/_VOICE/_FALLBACK` (`app/voice_entry._build_audio_tts`; needs `livekit-agents>=1.5.15`). This replaced the MiniMax `minimax.TTS` plugin + its `_OneSegmentTTS` one-segment workaround (a `1.2.9-plugin × agents-1.5.15` `start_segment()` crash fix) — Inference uses the native 1.5.15 streaming API, so the workaround is **deleted**. **MiniMax Speech (`/v1/t2a_v2`) is now ONLY the kernel-facing `Synthesizer` contract object** (`adapters/minimax_synthesizer.py`), not the audio you hear. `adapters/gemini_reasoner.py` / `openai_reasoner.py` are kept as alternate Reasoner backends behind the same guard.
- **Moss** retrieval live and **local-first** (per Moss's LiveKit guide, `docs.moss.dev`): the cloud plane (`service.usemoss.dev`) builds + stores the index, but `load_index` pulls it into the in-process `inferedge-moss-core` runtime and **every query runs locally, sub-10 ms, no cloud round-trip**. Embeddings = **Moss built-in `moss-minilm`** (`MOSS_EMBED_MODEL=moss-minilm`, the documented default — `create_index(…, model_id="moss-minilm")`): the runtime embeds the query in-process (weights fetched once from `models.moss.link` at load), keeping the whole hot path local + keyless. **Gemini `gemini-embedding-001` custom vectors are a fallback** (a cloud embed RPC per query, for when the model host is unreachable); the paths aren't mixable → **switching requires rebuilding the indexes**. Index limit is a PRICING tier (free=3, paid=unlimited).
- **Data-model rule: ONE index per data CATEGORY + a metadata `QueryOptions.filter`, NEVER one index per site/tenant** (research `docs/research/moss-index-design.md`). Live category indexes: `clarion-kb` (policy KB) · `clarion-site-structure` (KL (a): ALL sites' affordances, `{site}`-partitioned — `app/site_indexer.py` crawls read-only & writes, `SiteKnowledge` consults at PLAN, gated `CLARION_SITE_KNOWLEDGE=1`, fail-open) · `clarion-profile` (KL (c): facts+prefs) · `clarion-task-paths` (KL (b): completed-workflow episodes) — the last two `{user_id}`-filtered, behind `CLARION_MEMORY=1` (`retrieval/memory_moss.py`). Doc-id hashes the partition key; `load_index` before a filtered `search` (filter applies locally).
- **Moss usage decision (probed 2026-06-06):** keep ONE index per category + metadata filter (above); durable writes are **surgical + consent-gated**; the live working set stays in LangGraph state. Deliberately do **NOT** use Moss **sessions / `push_index`** or the per-turn **auto-capture** pattern — a wholesale push can't honor *no memory without a yes*. The `moss 1.2.0 → 1.4.0` on-device `session()` upgrade is a verified clean drop-in (same `inferedge-moss-core==0.14.0`, won't cascade into livekit) but **unadopted** — nothing it adds is needed for the event.
- **Knowledge-layer freshness/retention (probed 2026-06-06; freshness write-path BUILT 2026-06-07, live-page refresh deferred — briefs `research/graph-vs-vector-web-nav-2026-06-06.md` + `research/site-cache-freshness-best-practices-2026-06-06.md`):** the structure/`task-paths` cache is **advisory** — the live re-perceive is authority, so a stale cache costs only a wasted hop, never a wrong fact/action. **Freshness = verify-on-use, NOT TTL:** fingerprint each node (role+name+position+affordances), compare on the re-perceive we already run, **supersede on mismatch** (decay-rank, never hard-delete); accept a cached node only above a confidence threshold else **fail loud** (= the epistemic invariant — the line industry self-healing-locators cross between "works" and "hype"). **Retention on the consent axis:** public structure shared (`clarion-site-structure`) · private structure+paths **consent-gated per-user** · values **never**. A navigable site **GRAPH (if built) is a sidecar BESIDE Moss** (Moss 1.2.0 is vector-only, no graph API — verified) keyed to the same chunk ids, gated to path queries (graph helps multi-hop, hurts single-hop). **Crawl surface:** public → side-browser BFS · private/auth → read-only via the extension relay, lazy, **never CDP-attach the user's primary profile**. **BUILT (2026-06-07):** `app/structure_freshness.py` (value-blind structural fingerprint + `compare()` verdict) + `site_indexer` stamps each page's fingerprint/`indexed_at` on a **stable per-URL Moss id** (`GeminiMossIngest.ingest` gained `passage_metadata`/`id_basis`, backward-compatible) → a re-crawl **supersedes a changed page in place** (no stale-chunk rot), no TTL. **Auto-index trigger BUILT:** `app/auto_index.py` + an injected `on_orient` planner hook (`stages/graph.py`, wired in `runtime.build_stage_graph`) fire-and-forgets a background **read-only PUBLIC** crawl of the current host on the first ORIENT — cookie-less (can't touch the user's private pages), gated `CLARION_AUTO_INDEX=1` (default off), throttled per host, fail-open, denylisted-seed-skipped (`is_denied_url`). 16 tests, gate 212 green, sub-agent-verified. The **live-page (private-surface) auto-refresh stays DEFERRED** behind the surface/consent classifier — never auto-write a private/authenticated page's structure to the shared index. **AgentAtlas rejected as a dep** (pulls Supabase+OpenAI infra + a token-thrift goal we don't share — we re-perceive for correctness); concepts borrowed only.
- Pre-warm `load_index` on partial-STT so the first query already hits the in-memory sub-10 ms path (built-in embeds in-process — no ~2.7s embed RPC to overlap; that was the Gemini-path concern).

## Git
- Repo is rooted at `/Users/tk` (home), not at `agent/`. Always scope: `git -C /Users/tk/Desktop/conv-agent status -- <paths>`; never run an unscoped `git status` (it walks the entire home dir).
- Feature branches only; conventional commits (`feat:`/`fix:`/`docs:`); push only when asked.
