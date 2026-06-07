# Clarion — LIVE STATUS (read this first each session)

_Last updated: 2026-06-07 · **UNCOMMITTED on `main`** (recommend a feature branch + conventional commits before the next live run)._
_Latest (2026-06-07): **knowledge-layer freshness — the verify-on-use WRITE-PATH shipped.** New pure `app/structure_freshness.py` (a **value-blind** structural fingerprint + `compare()` unseen/fresh/stale verdict) + `site_indexer` now stamps each page's fingerprint/`indexed_at` on a **stable per-URL Moss id** (`GeminiMossIngest.ingest` gained `passage_metadata`/`id_basis`, backward-compatible) → a re-crawl **supersedes a changed page in place** (kills stale-chunk rot), **no TTL**. 8 new tests; **204 green**. The live-page auto-refresh stays **deferred** behind the surface/consent classifier (never auto-write a private page to the shared index). Brief: `research/site-cache-freshness-best-practices-2026-06-06.md`._
_Latest (2026-06-07, cont.): **auto-index TRIGGER shipped — the structure cache is now self-populating.** `app/auto_index.py` + an injected `on_orient` planner hook (`stages/graph.py`, wired in `runtime.build_stage_graph`): on the first ORIENT it **fire-and-forgets** a background **read-only PUBLIC** crawl of the current host — cookie-less (can't touch private pages), gated `CLARION_AUTO_INDEX=1` (default off), throttled per host, fail-open, denylisted-seed-skipped. Adversarially **sub-agent-verified (9/9 PASS → SHIP)**. **212 green.** The live-page (private-surface) refresh remains deferred behind the surface/consent classifier._
_Latest (cont., 2026-06-06): **de-hardcoded the negative-claim router + VERIFIED GAP-1 (the voice-plane grounding gap); 193 tests green.**_
- **De-hardcode (eliminated a banned keyword list).** Deleted the lexical `is_negative_claim` / `_NEGATION_MARKERS` table from `kernel/policy.py`. PROPOSE now routes a spoken negative through the `NegativeVerifier` on the model's OWN self-report — a new additive `StepProposal.asserts_absence: bool` (mirrors `alternatives`; wired through both reasoner adapters' schema + decode + decide-prompt → the **live MiniMax path** sets it). **SAFE:** the membership fence (#2) already bars speaking ANY non-grounded line, so the keyword list only ever chose the hedge/sourced-negative UX — removing it cannot reintroduce a false negative. Tests: `test_gate_wiring` (covered→spoken, uncovered→hedged) now drive it via the self-report; `test_negative_verifier` (5) asserts the routing-signal contract. **Leftover anti-pattern:** `negative_verifier._STOPWORDS` is the SAME banned shape — correct fix is the deferred **P4 entailment/embedding grounder** (blocked on an NLI host), so it was flagged, NOT half-fixed.
- **GAP-1 VERIFIED (code-traced) — the voice plane can speak ungrounded free text.** The kernel's epistemic fences gate the TASK plane's Facts + the `utterance` PROPOSE forms — but the LIVE voice plane (`app/voice_entry.py`: `AgentSession(llm=MiniMax-M3)` + `Agent(instructions=_INSTRUCTIONS, tools)`) **GENERATES** all spoken audio via `session.generate_reply()` (greeting ~:611, replies ~:629). The tools return grounded strings, but the LLM is only *instructed* ("speak the readback VERBATIM", "add NOTHING" — `_INSTRUCTIONS` :391-407), **not code-forced** → a spoken line CAN ad-lib/paraphrase/embellish past the gate. This is the **highest-leverage epistemic gap**; the invariant is structural in the task plane but prompt-only at the voice seam. **Fix (deferred = backlog Step-6 "DeliveryGate"):** TTS the exact kernel `utterance`, or membership-check the generated text against `grounded_facts` BEFORE TTS. Memory: `voice-plane-ungrounded-narration-gap`. Research backing: `research/agentic-browser-failures-vs-clarion-2026-06-06.md` (confident wrongness in free text = the field's #1 trust-killer; AXTree-grounding + hard consent = our two-clause edge, a combo no surveyed product ships).
_Earlier this session: **reliability + prevention pass — subagent-driven, 193 tests green** (plan: `docs/clarion-confidence-grounding-plan.md` §8). Five things landed: (1) **crash fix** — a degraded read-back no longer inherits an "irreversible" judgement (`kernel/irreversibility.classify`: a `read` is reversible by construction) → the `PolicyViolation` on `prop-0-0` is gone; (2) **give-up fix (F2)** — PROPOSE now feeds `decide_step` the **FULL live map**; the lexical `_topk_slice` pruned the goal-relevant control out of the enum-locked candidate set → untargetable → read-back → give up; (3) **abstain-and-clarify (the demo HERO beat)** — the Reasoner self-reports ambiguity via an additive `StepProposal.alternatives`; when set, PROPOSE emits a safe "which did you mean?" read-back instead of guessing (NO keyword lists — the model does the metacognition); (4) **default mode `fast`→`normal`** — every consequential step gates at CONSENT; (5) **trace logging** — kernel/executor decisions now hit `/tmp/clarion-worker.log` as `[task]` lines. New **CLAUDE.md rule: never hard-code word/keyword lists — derive meaning from the LLM/embeddings behind a port.** Two research briefs added (`docs/research/llm-confidence-rag-groundedness-2026-06-06.md`, `prevention-demos-showcase-2026-06-06.md`). See "Done this session — reliability + prevention pass" below._
_Earlier: **voice TTS swapped MiniMax → LiveKit Inference** (`inference.TTS`, native —
no per-provider key, routed through the LiveKit Cloud creds): default **Cartesia
Sonic-2** + automatic **Deepgram Aura-2** failover; knobs `CLARION_TTS_MODEL/_VOICE/
_FALLBACK` (`app/voice_entry._build_audio_tts`). Dropped the MiniMax `_OneSegmentTTS`
plugin workaround. The **brain stays MiniMax-M3** (Anthropic gateway). Tests green (190).
Earlier: provider swap → **MiniMax** (MiniMax-M3 brain + Speech 2.6-turbo voice),
wired through LiveKit; Deepgram STT + Gemini retrieval embeddings unchanged.
**Voice-LLM resilience:** M3's endpoint intermittently 5xx'd ("unknown error (1000)")
under load and the agent went SILENT — now `_build_llm()` wraps M3 (primary) +
`MiniMax-M2.7` in `llm.FallbackAdapter` (`MINIMAX_LLM_MODEL_FALLBACK`, `off` to
disable; verified: forced-fail primary → M2.7 answers, `reasoning_split` clean on both).
**Logging:** per-frame VAD/STT + `[asr] user` spam silenced; worker HUD lines no longer
double-POST to `ext.log` (SW skips sink on `fromWorker`); `clarion-up` reaps orphan tails.
**Debug HUD redesigned:** the on-page panel is now a LiveKit-style **status visualizer**
(a bar-orb that breathes/sweeps/bounces per agent state — idle·linking·listening·thinking·
speaking·error, driven off the real `[agent] old → new` lines) + an elegant event log
(category accents, level dots, drag/collapse/copy/clear). `hud.js`.
**Knowledge layer:** a read-only same-origin STRUCTURE crawler (`app/site_indexer.py`)
injects page affordances (headings + controls, NEVER live values) into ONE
`clarion-site-structure` Moss index partitioned by `{site}` metadata (per-CATEGORY,
not per-site — Moss `QueryOptions.filter`; `docs/research/moss-index-design.md`), and
is WIRED into PLAN via `SiteKnowledge` (gated `CLARION_SITE_KNOWLEDGE=1`, fail-open).
Active Moss project is a clean dedicated one; `clarion-kb` + `clarion-site-structure`
built + verified (Gemini custom embeds, ~1ms in-mem). PyTorch advisory silenced
(turn-detector uses onnxruntime, not torch)._

This is the single source of truth for **where we are and what's left**. Keep it
current: when you finish or change something, edit this file in the same commit.

**Competitive landscape** (scanned 2026-06-06): the exact niche — a blind-first voice
co-pilot *extension* — has **no product with traction** (closest rivals are single-digit-
install demos: Phantom 6, YourVoice 28). Real alternatives are apps/services (Be My Eyes
~1M, Aira) and agentic browsers (Comet/Atlas/Neon — which already ship *soft* consent +
citation). Clarion's edge = those as a **hard invariant**, AXTree-first (rivals are
vision/coordinate-first → can't cite their source). Full scan + install counts:
`research/chrome-extension-competitors-2026-06-06.md`.

---

## TL;DR (the one paragraph)

The **task plane is now DE-HARDCODED**. The "pay my electric bill" AUTH→…→CONFIRM
topology is **deleted**: a generic LLM (`GeminiReasoner`, `thinking_budget=0`)
plans the goal and decides each grounded step behind a **frozen `Reasoner` port**;
the LangGraph kernel acts and **enforces the two invariants in code**. The four
de-hardcoding systems shipped this wave — **Reasoner** (port + Gemini, Qwen/Nebius
failover, post-decode guard) · **PairedFact** (geometric label↔value + membership +
same-cycle pairing fences) · **dual-signal irreversibility gate** (escalate-only,
UNKNOWN-gates-Fast, NegativeVerifier honest-decline) · **generic anchored done-check**
(code-selected, not say-so). **Proven end-to-end on TWO real gov sites, ZERO
site-specific code** (autonomous Playwright + live Gemini): usa.gov benefits
(read-only, grounded values + real citations, anchor-certified) AND weather.gov (a
benign form — filled, then the submit classified UNKNOWN → consent **hard-stop** →
declined, never submitted). **178 tests green** + a goal-agnostic invariant spec with
red-before-green proven by mutation. **Testing rule (locked):** real sites only —
never the `web/demo-site` clone.

---

## Real vs stub inventory (the honest map)

| Piece | State | Evidence / location |
|---|---|---|
| Voice: LiveKit · Deepgram STT · **MiniMax-M3 LLM (M2.7 failover) · LiveKit Inference TTS (Cartesia Sonic-2 + Deepgram Aura-2 failover)** | **REAL, wired** | `app/voice_entry.py` — MiniMax via the LiveKit `minimax` plugin; STT stays Deepgram. **`_build_llm()` = `llm.FallbackAdapter([M3, MiniMax-M2.7])`** so an M3 5xx fails OVER instead of going silent (both share the `reasoning_split`-wrapped client). **Plugin needs `MINIMAX_GROUP_ID` + `voice_id` (not `voice`); model/voice enums differ from the raw t2a_v2 synth → reads `MINIMAX_PLUGIN_TTS_MODEL/_VOICE`** |
| Voice-conversation observability (ASR heard · agent state · tool calls · errors) | **REAL — on the HUD panel + unified log; deduped** | `voice_entry.py` `hud()` → LiveKit room-data (`clarion-log` topic) → `offscreen.js` `DataReceived` → SW `pushHud`; the worker also POSTs to the sink so `/tmp/clarion-ext.log` is ONE stream — and the HUD round-trip now skips the sink (`fromWorker`) so worker lines aren't double-logged. **Per-frame VAD/STT metrics + `[asr] user` state are silenced** (re-enable in `voice_entry.py` for profiling). **HUD panel = LiveKit-style status visualizer** (`hud.js`): the bar-orb reflects the live agent state machine off the `[agent] old → new` lines (reads the *new* state, right of the arrow), `setHudStatus` covers the attach/voice-connect/teardown edges the machine doesn't; the log is category-coloured + draggable + sanitized (role label → "Clarion") |
| Perception (CDP AXTree → numbered map), lazy-stamp | **REAL, cheap** | `actuator/pipeline.py`, `actuator/*actuator.py` (perceive 0 stamp round-trips; `reperceive_node`) |
| Actuator act (click/fill/navigate over CDP) + `filled` record | **REAL** | native-setter fills stamp `state["filled"]` by node_id |
| Kernel loop GROUND→VERIFY→PROPOSE→⟨GATE⟩→CONSENT→ACT→CONFIRM | **REAL** | `kernel/graph.py` |
| ORIENT `read_screen` (grounded page readout) | **REAL, live-verified** | `read_screen` + `summarize_ax_tree`/`describe_page` |
| Goal source | **REAL (from confirmed user intent)** | `voice_entry.py` `set_goal`; no baked default |
| **Task PLAN / topology** | **✅ REAL — LLM Reasoner, generic executor** | `Reasoner.plan_goal`→subgoals; `stages/graph.py` generic executor (no baked topology) |
| **Next-step decision (PROPOSE)** | **✅ REAL — `Reasoner.decide_step` over the FULL live map** | `kernel/graph.py`; off-page index/value rejected by `kernel/reasoner_guard.py`. **Lexical top-K slice REMOVED** (2026-06-06: it pruned the goal-relevant control → "give up"); `_topk_slice` retained off-path for a future semantic `ContextRanker` |
| **Abstain-and-clarify (ambiguous goal)** | **✅ REAL — model self-reports (the demo hero beat)** | additive `StepProposal.alternatives` (`contracts/state.py`); reasoner schema/decode/prompt populate it (`adapters/gemini_reasoner.py` shared builders + `openai_reasoner._DECIDE_SYSTEM` → live MiniMax path); `kernel/graph.py` PROPOSE `(2b)` emits a safe `read` "which did you mean?" naming the rival controls — never guesses. No keyword lists |
| **Consent default** | **✅ `normal` mode** | `app/voice_entry.py` — every consequential step gates at CONSENT (was `fast`); pure read-backs still flow |
| Reasoner adapter | **✅ REAL — MiniMax-M3** | `adapters/minimax_reasoner.py` (default; OpenAI-compatible `MiniMax-M3` via `OpenAIReasoner`, same guard) + same-provider MiniMax failover (`gov_proof._build_reasoner`). `gemini_reasoner.py`/`openai_reasoner.py` kept as alternates |
| **GROUND facts (page values) + PairedFacts** | **✅ REAL (page-grounded)** | `app/page_retriever.py`; `actuator/pipeline.py` `extract_text_facts`/`extract_paired_facts` (geometric, both halves real node ids) |
| **Epistemic fences (task plane)** | **✅ REAL** | `kernel/policy.py` membership (`is_speakable_value`) + pairing (`pairing_backs`); `NegativeVerifier` hedge, now routed by the model's self-reported `StepProposal.asserts_absence` (lexical `is_negative_claim` DELETED) |
| **Spoken-output grounding (voice plane)** | **⚠️ PROMPT-gated, NOT code-gated (GAP-1)** | `app/voice_entry.py` — the MiniMax-M3 voice LLM GENERATES audio via `session.generate_reply()`; tools return grounded strings but the LLM is only *instructed* (`_INSTRUCTIONS`) to relay verbatim → a spoken line CAN ad-lib past the task-plane fences. Fix = the **DeliveryGate** (speak the exact kernel `utterance` / membership-check pre-TTS). The thing to WATCH on every live run (diff spoken vs `grounded_facts` in `/tmp/clarion-worker.log`) |
| **Irreversibility gate** | **✅ REAL — dual-signal** | `kernel/irreversibility.py` (structural pre-screen escalate-only; UNKNOWN-on-no-undo gates Fast). **A `read` action is reversible by construction** (2026-06-06 fix: stops a degraded read-back from inheriting an abandoned step's "irreversible" judgement → the `prop-0-0` crash) |
| **Done-check** | **✅ REAL — code-selected, anchored** | `stages/checks.py` 5 generic checks + URL anchor; hardcoded registry DELETED |
| Retrieval (Moss, KB) | **embedding path config-gated; built-in BLOCKED** | `retrieval/`; `MOSS_EMBED_MODEL` selects **Gemini custom** (working — custom vectors bypass the model host) or **built-in `moss-minilm`/`moss-mediumlm`** (wired but DEAD: `models.moss.link` still can't serve the model to the moss runtime — `load_index` fails on `.../config.json`, verified 2 ways 2026-06-06). **The active Moss project (in `agent/.env`) is now a clean dedicated one with `clarion-kb` built + smoke-verified** (Gemini custom embeds, ~1ms in-mem). NB the per-project index limit is a PRICING tier (free Developer=3, paid=Unlimited), not a hard wall. **Moss supports query-time metadata filtering** (`QueryOptions.filter`, `$eq`/`$in`/… on a loaded index) → website structure now lives in ONE `clarion-site-structure` index partitioned by `{site}` metadata, NOT one index per site (`docs/research/moss-index-design.md`) |
| **Website STRUCTURE index** (knowledge layer a) | **WIRED into PLAN (consult) + built/proven live; ONE category index** | `app/site_indexer.py`: read-only same-origin crawl → `describe_page` affordances (no values) → the SINGLE `clarion-site-structure` index, each chunk tagged `{site,url}`. `SiteKnowledge.context_facts` is consulted by the planner (`stages/graph.py` folds a SITE MAP into the plan `orient`) and scopes by metadata `filter` (`site $eq <host>`), gated by `CLARION_SITE_KNOWLEDGE=1`, fail-open. **Per-category-not-per-site** (research: `docs/research/moss-index-design.md`; Moss `QueryOptions.filter`). Proven on usa.gov (consult surfaces `/complaints` first; non-matching site filter → 0) |
| **User memory: facts · preferences · workflow episodes** (knowledge layer b+c) | **✅ REAL — Moss-backed, behind `CLARION_MEMORY=1`** | `retrieval/memory_moss.py` — **category indexes + `user_id` filter** (matches the structure-index research): `clarion-profile` (facts+prefs) + `clarion-task-paths` (episodes), `kind`-discriminated, scoped by `QueryOptions.filter` `user_id $eq <uid>`. `Memory.recall` warm-starts the plan (`stages/graph.py` planner → `prior_plan_hint`; **fixed the `import os` crash** that nuked recall when `CLARION_MEMORY=1`); `gov_proof` writes a finished run as a `WorkflowEpisode`; `app/remember.py` = consent-gated "remember?" capture (secrets never offered → *no memory without a yes*). **NOW WIRED:** the end-of-flow remember offer is a `_REMEMBER` stage node — the executor harvests filled fields (`{node_id:value}`), the runtime injects the secret-suppressing nominator (`build_stage_graph(remember_nominate=…)`, only under `CLARION_MEMORY=1`), and the batched `ConsentRequest` surfaces through the **existing** stage-graph `interrupt()` the voice loop already speaks/resumes — write goes through `Memory.write_preference` ONLY on an explicit yes (tested: `test_executor.py::test_remember_offer_*`). **Firewall:** `Recall` has no `source_node_id` → structurally unspeakable, re-grounded live. **Pending:** a live episode round-trip. Spec: `docs/clarion-memory-design.md` |

---

## Done this session — reliability + prevention pass (2026-06-06, UNCOMMITTED on `main`)

The reframe: **the product is failure-PREVENTION** — never corrupt the page, never go to the wrong place, never speak what isn't on screen. The give-up was the system trying to prevent a wrong move (ungracefully); the fix makes that refusal graceful + legible, and the **abstain path is the demo's hero**. Driven via subagents (one clear task each), each verified by re-running the full no-network gate. Plan + execution log: `docs/clarion-confidence-grounding-plan.md` (§8).

**IMPLEMENTED:**
- **Crash fix (F1)** — `kernel/irreversibility.classify`: a `read` action is `reversible` by construction, so a degraded read-back can't inherit the model's "irreversible" judgement of an abandoned navigate. Kills the `PolicyViolation` on `prop-0-0`. +regression test.
- **Give-up fix (F2)** — `kernel/graph.py` `propose`: feeds `decide_step` the **FULL live map** (was the lexical 12-of-46 `_topk_slice`, which pruned the goal-relevant control out of the enum-locked candidate set → untargetable → read-back → give up). `reasoner_guard` still fences hallucinated indices. `_topk_slice` retained off-path.
- **Abstain-and-clarify (P3, hero beat)** — additive `StepProposal.alternatives` (`contracts/state.py`); reasoner self-reports ambiguity (schema/decode/prompt in `adapters/gemini_reasoner.py` shared builders + `openai_reasoner._DECIDE_SYSTEM`, so the live MiniMax path is covered); PROPOSE `(2b)` block emits a safe `read` "which did you mean?" naming the rival controls. 2 new tests. **No keyword lists — the model does the metacognition.**
- **Default mode `fast`→`normal`** (`app/voice_entry.py` ×2) — every consequential step gates at CONSENT (closes the silent-auto-proceed safety gap).
- **Trace logging (P1)** — `StageGraphRunner._log_trace` → `[task]` lines in `/tmp/clarion-worker.log` (decide_ms, target, classification, done, gave_up, abstained), whitelisted fields, best-effort.
- **CLAUDE.md rule** — *NEVER hard-code word/keyword lists (stopwords, name/intent matchers) to classify/rank/route — derive meaning from the LLM/embeddings behind a port.*
- **Research briefs** — `docs/research/llm-confidence-rag-groundedness-2026-06-06.md` (confidence + RAG groundedness methods: UniCR, CISC, SelfCheckGPT-NLI, RAGAS, Vectara HHEM, FRANQ) and `prevention-demos-showcase-2026-06-06.md` (what Operator/Mariner/Computer-Use/PageGuide demoed + the demo-craft + the 4-beat Clarion narrative). 13 YouTube videos ingested → yt-rag namespace `yt_agent_prevention_hitl`.
- **Tests: 193 passed, 10 deselected** (was 191).

---

## Done earlier — the Clarion-PE/G migration (commits on `feat/clarion-extension`)

Strangler migration of `docs/clarion-architecture.md`; every step validated by
behavior on a real site with `load_dotenv` keys, never an exit code.

- **`ec8a265`** S0/S1 latency + Gap-1: lazy-stamp `perceive()` (stamp round-trips
  90→0 on usa.gov over the extension transport, cold ~297→38ms) + page-grounded
  GROUND (kills the `$84.32` fixture).
- **`721cb3e`** Wave A — contracts spine: `Fact.id`, `PairedFact`, `Subgoal`,
  `StepProposal`, the frozen `Reasoner` ABC, `FakeReasoner`, the pure post-decode
  `kernel/reasoner_guard.py`. Live spike on usa.gov (48 nodes, guard fails-closed).
- **`f668de4`** Wave B — geometric `PairedFact` (aria-labelledby/`for`/dom-ancestry/
  shared-row) + ranker→hint + `query_all` unfiltered fallback + value-fact harvest.
  Proven on a real ssa.gov table.
- **`3457c05`** Wave B — `GeminiReasoner` (the only LLM home): structured output,
  enums over live indices/Fact ids, guard reused.
- **`641f841`** Wave C — **de-hardcode the task plane**: PROPOSE via `decide_step`
  over the top-K slice (name-matcher deleted); `plan_goal` via the Reasoner; generic
  executor (no baked topology); VERIFY set-membership + pairing fence. RESCUE +
  bounded replanner kept.
- **`f9ebbc6`** latency: `gemini-3.5-flash` `thinking_budget=0` (decode 36–121s →
  ~2s, the auto-thinking fix; config knob, not a model swap) + `OpenAIReasoner`
  (Qwen/Nebius) failover. A/B: Gemini(thinking=0) ~2s + native enums beat Qwen ~5s.
- **`7276a26`** Wave C — dual-signal irreversibility gate (escalate-only structural
  pre-screen, UNKNOWN-gates-Fast) + `NegativeVerifier` honest-decline + Fast-cap.
  No name-keyword list anywhere.
- **`fff2148`** Wave C — generic anchored done-check (5 site-agnostic checks + URL
  anchor); hardcoded `DONE_PREDICATES` registry deleted, `detect_rescue` kept.
- **`e0c5f32`** Wave C — generic invariant spec replaces the 52+ topology assertions;
  red-before-green proven by mutation.
- **`90a8eef`** Wave D — actuator stamps `state["filled"]` (the AX tree drops the
  typed value) so the generic done-check sees a real fill; no-op invariant preserved.
- **`626c889`** Wave D — **the gov-proof driver**: `app/gov_proof.py` (generic,
  autonomous, `ResilientReasoner` failover, consent policy: approve reversible /
  reject irreversible-or-unknown). `app/hero_harness.py` retired to an import-clean
  shim. **Proven on usa.gov + weather.gov.**

---

## REMAINING / leftover functionalities (the next-phase backlog, ordered)

**Reliability + prevention pass — remaining** (plan: `docs/clarion-confidence-grounding-plan.md`):
- **Commit the work** — P0–P3 + the two research briefs + the plan are UNCOMMITTED on `main`. Branch + conventional commits before the next live run.
- **Live gov-page verify** (now observable via the new `[task]` trace logging): confirm PROPOSE targets "Food assistance" (give-up fixed) and the ambiguous "Food assistance vs Food safety" **abstains** with a spoken "which did you mean?".
- **P2b — semantic `ContextRanker` (DEFERRED — blocked on a fast embedder host).** Replace the removed lexical slice with embedding-similarity ranking (goal↔control), recall-oriented, behind an injected port; default stays full-map. Blocked: `moss-minilm` isn't standalone, Gemini embed ≈2.7s/call. Doubles as the P3 similarity-confidence signal.
- **P4 — claim-level entailment grounder (DEFERRED — blocked on an NLI host).** For SYNTHESIZED/negative spoken lines, verify entailment vs the perceived region (local NLI / Vectara HHEM) before speaking; abstain/hedge otherwise. Tiered — verbatim membership reads skip it. Upgrades the epistemic clause from membership → entailment (FRANQ: keep faithfulness, not factuality).
- **Recall-on-safe-behavior metric** on the U1 panel (demo): % of ambiguous/ungrounded cases correctly asked/abstained/gated (Operator-style framing; `docs/research/prevention-demos-showcase-2026-06-06.md`).
- **(latent, not the demo bug)** `stages/graph.py::_drive_kernel` never persists `_kernel_threads` when it re-surfaces a consent interrupt (the `interrupt()` raises first), so each parent resume spins a fresh inner-kernel thread + re-runs GROUND→PROPOSE (an extra Reasoner call). Wasteful, not incorrect.

1. **Step-6 latency layer — SpeculationController + DeliveryGate (for the <800ms
   voice turn).** `decide_ms` is ~2s; the task plane is correct but the LIVE voice
   loop has ~2s think-gaps. Pre-fire perceive+embed+speculative decide on partial
   STT against an AXTree-hash snapshot; the DeliveryGate re-checks the target node
   between "yes" and act (discard stale, never click). Behind a flag; lands last.
   `actuator/reperceive_node` is already in place for it.
2. **Live-voice / extension end-to-end run.** The autonomous proof is done; the
   product-path proof (extension on a real tab, press shortcut, speak the goal) is
   not yet re-run on the de-hardcoded stack. One human step = the shortcut.
3. **Actuator AX enrichment for the gate.** The structural pre-screen over-gates to
   UNKNOWN because `type=submit` / `<form>` membership / off-origin nav aren't on
   `AxNode.state`. A small additive AX/DOM stamp would let it escalate a submit to
   `irreversible` (not merely `unknown`) without a name match. TODO in
   `kernel/irreversibility.py::_structural_prescreen`.
4. **Knowledge layer** (the user's vision): graphs + embedding DBs over
   **(a) website functionalities** (seed = `PageReadout.affordances`) — ✅ a read-only
   STRUCTURE crawler shipped as a SPIKE (`app/site_indexer.py`): same-origin BFS →
   `describe_page` affordances (NEVER live values) → the SINGLE `clarion-site-structure`
   index, partitioned by `{site}` metadata (per-category, not per-site —
   `docs/research/moss-index-design.md`), proven on usa.gov. ✅ **WIRED into PLAN**:
   `SiteKnowledge.context_facts` (gated `CLARION_SITE_KNOWLEDGE=1`, fail-open) is
   consulted by the planner, scoped by a `site` metadata filter, folding a SITE MAP
   into the plan `orient` so the Reasoner can pick which page to navigate to. Next: a
   background crawl-on-activation + extend the consult into PROPOSE; apply the same
   category+metadata model to **(b) task paths** (the subgoal plans we run) and
   **(c) user profile/traits**
   (the `Memory`/`Profile` port). Categorize + persist + reuse across sites.
   **Freshness/retention decided (probed 2026-06-06) + verify-on-use WRITE-PATH + AUTO-INDEX TRIGGER BUILT 2026-06-07 (`app/structure_freshness.py` + `site_indexer` stable-per-URL ids + supersede; `app/auto_index.py` + `on_orient` planner hook → background public cookie-less crawl on first ORIENT, gated `CLARION_AUTO_INDEX=1`; 16 tests, 212 green, sub-agent-verified; live-page private-surface refresh deferred — briefs `research/graph-vs-vector-web-nav-2026-06-06.md` + `research/site-cache-freshness-best-practices-2026-06-06.md`):** the cache is **advisory** (live re-perceive = authority → stale = a wasted hop, never a wrong fact/action); freshen by **verify-on-use, not TTL** (per-page structural fingerprint → supersede on mismatch, decay not delete), accept only above a **confidence gate else fail loud** (= the epistemic invariant). Retention on the **consent axis**: public structure shared · private structure+paths consent-gated per-user · values never. A navigable **site graph = sidecar BESIDE Moss** (vector-only) keyed to chunk ids, gated to path queries. Crawl surface: public → side-browser BFS · private/auth → read-only via the extension relay, **never CDP-attach the user's primary profile**. **AgentAtlas evaluated + rejected as a dep** (Supabase+OpenAI infra + a token-thrift goal we don't share); concepts borrowed only (`validate()` health lifecycle + scope keys).
5. **Data-model simplification pass.** Audit `ClarionState`/`_PlanState` + value
   objects; keep only what we track (no bloat).

---

## Points to FIX / TEST

- [x] **Reliability pass green** — `pytest clarion -q` **193 passed, 10 deselected** after the crash fix (read→reversible), full-map PROPOSE, and abstain-and-clarify.
- [ ] **Live gov-page verify (reliability pass)** — with the new `[task]` trace logging up: PROPOSE targets "Food assistance" (give-up fixed); the ambiguous "Food assistance vs Food safety" **abstains** ("which did you mean?"); a consequential step gates at CONSENT (normal mode).
- [ ] **GAP-1 live-watch (epistemic) — the headline check.** On EVERY live run, diff what was SPOKEN against `grounded_facts` in `/tmp/clarion-worker.log`. PASS = every spoken sentence is a kernel-formed say (no ad-lib). FAIL = the MiniMax-M3 voice LLM paraphrased/added an ungrounded line → build the **DeliveryGate** (speak the exact kernel `utterance`, or membership-check pre-TTS). This is the highest-leverage gap (`voice-plane-ungrounded-narration-gap`).
- [ ] **Live-verify the de-hardcoded negative router** — MiniMax-M3 sets `asserts_absence` honestly: a real negative probe ("is there any fee?") with no grounded `absent` fact → **HEDGE** ("I couldn't confirm that either way…"), never a confident "no fee".
- [ ] **Decide the embedder / NLI host** to unblock P2b (semantic ranker) + P4 (entailment grounder, which also retires `negative_verifier._STOPWORDS`); else ship full-map + model-self-report (the current state).
- [ ] **Commit** the uncommitted reliability-pass work (P0–P3 + briefs + plan) on a feature branch.
- [x] **De-hardcoding proven end-to-end on REAL gov sites** (usa.gov read-only +
      weather.gov form), ZERO site-specific code, every invariant live.
- [x] `pytest clarion -q` green (**178 passed, 10 deselected**) + goal-agnostic
      invariant spec (red-before-green proven by mutation). Green AFTER the MiniMax swap.
- [x] **Provider swap → MiniMax** (LLM + voice): `MinimaxReasoner` (MiniMax-M3,
      OpenAI-compatible) is the default decider + same-provider failover;
      `MinimaxSynthesizer` (Speech 2.6-turbo, `/v1/t2a_v2` streaming PCM) is the
      kernel TTS; LiveKit voice plane uses `minimax.LLM` + `minimax.TTS`. STT stays
      Deepgram; retrieval keeps Gemini embeddings (KB already built on them).
- [ ] **MiniMax live-verify (pending key):** `scripts/set-minimax-key.sh` → key in
      `agent/.env`, then `pip install -e ".[spike]"` (pulls `livekit-plugins-minimax`
      + `httpx`) → `python -m clarion.app.gov_proof` (M3 decides) + a voice run
      (hear the Speech 2.6 voice). Confirm M3 honors the structured-output schema
      (else `OpenAIReasoner` auto-falls back to `json_object`).
- [ ] **Live-voice product-path proof** on the de-hardcoded stack (extension on a
      real tab; hear the readback + the per-step consent + the irreversible hard-stop).
- [ ] **Step-6 speculation** before the live voice demo (hide the ~2s decode).
- [x] **Qwen/Nebius retired from the default path** — failover is now MiniMax, so the
      pasted `NEBIUS_API_KEY` is unused (still: rotate it, it leaked in chat).
- [ ] `python scripts/copy_lint.py <file>` on any new copy (no "assistant/helper/assist").

**Testing rule (LOCKED):** never test on the `web/demo-site` clone — only ACTUAL
real sites. Acceptance = grounded readback + per-step consent + honest decline on a
real page; NOT a completed irreversible action (we drive to the gate and stop).

---

## How to run + LOGS

```bash
scripts/clarion-up.sh                 # rotates logs → .prev, starts logsink+broker+worker, opens Chrome on usa.gov/benefits
                                      # SHARED COCKPIT: launches Chrome for Testing (NOT branded Chrome — see gotcha)
                                      # durable profile (~/.clarion/chromium-profile-durable — logins persist)
                                      # + CDP on :9222 (override CLARION_CHROME_PROFILE / CLARION_CDP_PORT)
scripts/clarion-status.sh             # ONE command: ports + procs + tail of every log (run this first to see state)
scripts/clarion-down.sh               # stop everything (reaps the worker's whole job tree)

# Autonomous de-hardcoded gov proof (no voice, real Gemini + Playwright):
cd agent && .venv/bin/python -m clarion.app.gov_proof   # the generic TAS driver (app/gov_proof.py)
```

**Logs** (rotated to `*.prev` on each `clarion-up`):
- `/tmp/clarion-worker.log` — agent worker; phases tagged `[loop]`, latency `[lat]`, tools `executing tool`.
- `/tmp/clarion-broker.log` — relay broker (8771 ext / 8773 agent); connect + session.start cache/replay.
- `/tmp/clarion-ext.log` — browser SW + HUD via the sink (`scripts/clarion-logsink.py`).

**Restarting ONLY the worker (to load code changes without touching Chrome/the extension):**
- Reap first: `pkill -if "clarion.app.voice_entry"; pkill -if "from multiprocessing.spawn"` (orphan job subprocs steal dispatches).
- Start detached: `CLARION_ACTUATOR=extension nohup .venv/bin/python -m clarion.app.voice_entry dev >>/tmp/clarion-worker.log 2>&1 &` inside a `( … )` subshell.

**Shared cockpit (observe the human's tab):** the human logs in by hand; you SEE login state via
Playwright `connect_over_cdp("http://localhost:9222")` — but **only while the extension is idle**
(before the shortcut). A live CDP session and the extension's `chrome.debugger` cannot share a tab,
so once it's driving, read the LOG FILES, not CDP. Detach (`browser.close()` on the Playwright side)
before pressing the shortcut.

**Operational gotchas (cost real time before — see project memory):**
- **The mic is the OS default, which is often a VIRTUAL device** → ASR hears silence and the
  panel shows nothing heard. This machine's default input is `MMAudio Device` (Transport: Virtual),
  not the real `MacBook Pro Microphone`. The offscreen doc now AUTO-PREFERS a real mic (skips
  MMAudio/Teams/loopback/virtual), logs the device list + chosen device + an audio-level check to
  the HUD, and the worker logs every transcript as `[asr] HEARD ✓ final: …`. Grep `/tmp/clarion-worker.log`
  for `[asr]`; override the pick with `CLARION_MIC_MATCH="MacBook Pro Microphone"`.
- **Branded Google Chrome REMOVED `--load-extension`** (abuse vector; verified Chrome 148, 2026-06-05).
  The CDP replacement `Extensions.loadUnpacked` needs `--remote-debugging-pipe` (kills our CDP port).
  Fix: `clarion-up.sh` launches **Google Chrome for Testing** (Playwright's bundled binary —
  `p.chromium.executable_path`), which still honors `--load-extension`. Our SW loads as
  `chrome-extension://…/service-worker.js`. Branded Chrome = manual `Load unpacked` fallback only.
- A Chrome already running on the durable profile makes a new `clarion-up.sh` launch a no-op for its
  flags (no fresh `--load-extension`, no CDP) — it just opens a tab in the live instance. Quit that
  Chrome first for a clean relaunch (the script now warns when :9222 is already listening).
- Same-profile Chrome relaunch does NOT reload the extension → prove fresh SW code by a NEW line in `/tmp/clarion-ext.log`.
- `chrome.debugger` attach fails while DevTools is open on the tab.
- Killing a job leaves the LiveKit room's agent slot occupied → **delete the room** to force a clean dispatch (`api.LiveKitAPI(...).room.delete_room(...)`, creds in `agent/.env`).
- Gemini AI-Studio TTS ~100 req/min → `429` under load; tool calls still run. Reasoner decode (Gemini, thinking=0) ~2s; occasional `503 high-demand` → Qwen/Nebius failover (`ResilientReasoner`).

---

## Acceptance for "the whole thing works end-to-end"

1. **[DONE — autonomous]** A generic driver states a goal-derived plan, reads
   page-grounded facts with citations, gates every step, and **hard-stops at the
   irreversible step** on a real gov site, ZERO site-specific code.
2. **[DONE]** On a page that doesn't afford the goal, it declines honestly /
   hedges an uncovered negative (no fake "task complete", no confident "no late fee").
3. **[DONE]** `pytest clarion -q` green; invariant spec catches a silent weakening.
4. **[OPEN]** Live-voice: `clarion-up.sh` → shortcut → hear the readback → speak a
   goal → per-step consent + the irreversible hard-stop, heard end-to-end (needs the
   Step-6 speculation to hide the ~2s decode).
