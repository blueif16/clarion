# Clarion — LIVE STATUS (read this first each session)

_Last updated: 2026-06-06 · branch `feat/clarion-extension`._
_Latest: provider swap → **MiniMax** (MiniMax-M3 brain + Speech 2.6-turbo voice),
wired through LiveKit; Deepgram STT + Gemini retrieval embeddings unchanged. Tests
green; live-verify pending the MiniMax key (`scripts/set-minimax-key.sh`).
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
**Knowledge layer (spike):** a read-only same-origin STRUCTURE crawler
(`app/site_indexer.py`) injects page affordances (headings + controls, NEVER live
values) into a per-site Moss index (`clarion-site-<host>`) — proven live on usa.gov
with round-trip retrieval. Active Moss project set to a clean dedicated one;
`clarion-kb` (re)built + smoke-verified there (Gemini custom embeds, ~1ms in-mem)._

This is the single source of truth for **where we are and what's left**. Keep it
current: when you finish or change something, edit this file in the same commit.

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
| Voice: LiveKit · Deepgram STT · **MiniMax-M3 LLM (M2.7 failover) · MiniMax Speech 2.6-turbo TTS** | **REAL, wired (live-verify pending key)** | `app/voice_entry.py` — MiniMax via the LiveKit `minimax` plugin; STT stays Deepgram. **`_build_llm()` = `llm.FallbackAdapter([M3, MiniMax-M2.7])`** so an M3 5xx fails OVER instead of going silent (both share the `reasoning_split`-wrapped client). **Plugin needs `MINIMAX_GROUP_ID` + `voice_id` (not `voice`); model/voice enums differ from the raw t2a_v2 synth → reads `MINIMAX_PLUGIN_TTS_MODEL/_VOICE`** |
| Voice-conversation observability (ASR heard · agent state · tool calls · errors) | **REAL — on the HUD panel + unified log; deduped** | `voice_entry.py` `hud()` → LiveKit room-data (`clarion-log` topic) → `offscreen.js` `DataReceived` → SW `pushHud`; the worker also POSTs to the sink so `/tmp/clarion-ext.log` is ONE stream — and the HUD round-trip now skips the sink (`fromWorker`) so worker lines aren't double-logged. **Per-frame VAD/STT metrics + `[asr] user` state are silenced** (re-enable in `voice_entry.py` for profiling). **HUD panel = LiveKit-style status visualizer** (`hud.js`): the bar-orb reflects the live agent state machine off the `[agent] old → new` lines (reads the *new* state, right of the arrow), `setHudStatus` covers the attach/voice-connect/teardown edges the machine doesn't; the log is category-coloured + draggable + sanitized (role label → "Clarion") |
| Perception (CDP AXTree → numbered map), lazy-stamp | **REAL, cheap** | `actuator/pipeline.py`, `actuator/*actuator.py` (perceive 0 stamp round-trips; `reperceive_node`) |
| Actuator act (click/fill/navigate over CDP) + `filled` record | **REAL** | native-setter fills stamp `state["filled"]` by node_id |
| Kernel loop GROUND→VERIFY→PROPOSE→⟨GATE⟩→CONSENT→ACT→CONFIRM | **REAL** | `kernel/graph.py` |
| ORIENT `read_screen` (grounded page readout) | **REAL, live-verified** | `read_screen` + `summarize_ax_tree`/`describe_page` |
| Goal source | **REAL (from confirmed user intent)** | `voice_entry.py` `set_goal`; no baked default |
| **Task PLAN / topology** | **✅ REAL — LLM Reasoner, generic executor** | `Reasoner.plan_goal`→subgoals; `stages/graph.py` generic executor (no baked topology) |
| **Next-step decision (PROPOSE)** | **✅ REAL — `Reasoner.decide_step` over top-K slice** | `kernel/graph.py`; off-page index/value rejected by `kernel/reasoner_guard.py` |
| Reasoner adapter | **✅ REAL — MiniMax-M3** | `adapters/minimax_reasoner.py` (default; OpenAI-compatible `MiniMax-M3` via `OpenAIReasoner`, same guard) + same-provider MiniMax failover (`gov_proof._build_reasoner`). `gemini_reasoner.py`/`openai_reasoner.py` kept as alternates |
| **GROUND facts (page values) + PairedFacts** | **✅ REAL (page-grounded)** | `app/page_retriever.py`; `actuator/pipeline.py` `extract_text_facts`/`extract_paired_facts` (geometric, both halves real node ids) |
| **Epistemic fences** | **✅ REAL** | `kernel/policy.py` membership (`is_speakable_value`) + pairing (`pairing_backs`); `NegativeVerifier` hedge |
| **Irreversibility gate** | **✅ REAL — dual-signal** | `kernel/irreversibility.py` (structural pre-screen escalate-only; UNKNOWN-on-no-undo gates Fast) |
| **Done-check** | **✅ REAL — code-selected, anchored** | `stages/checks.py` 5 generic checks + URL anchor; hardcoded registry DELETED |
| Retrieval (Moss, KB) | **embedding path config-gated; built-in BLOCKED** | `retrieval/`; `MOSS_EMBED_MODEL` selects **Gemini custom** (working — custom vectors bypass the model host) or **built-in `moss-minilm`/`moss-mediumlm`** (wired but DEAD: `models.moss.link` still can't serve the model to the moss runtime — `load_index` fails on `.../config.json`, verified 2 ways 2026-06-06). **The active Moss project (in `agent/.env`) is now a clean dedicated one with `clarion-kb` built + smoke-verified** (Gemini custom embeds, ~1ms in-mem). NB the per-project index limit is a PRICING tier (free Developer=3, paid=Unlimited), not a hard wall. **Moss supports query-time metadata filtering** (`QueryOptions.filter`, `$eq`/`$in`/… on a loaded index) → website structure now lives in ONE `clarion-site-structure` index partitioned by `{site}` metadata, NOT one index per site (`docs/research/moss-index-design.md`) |
| **Website STRUCTURE index** (knowledge layer a) | **WIRED into PLAN (consult) + built/proven live; ONE category index** | `app/site_indexer.py`: read-only same-origin crawl → `describe_page` affordances (no values) → the SINGLE `clarion-site-structure` index, each chunk tagged `{site,url}`. `SiteKnowledge.context_facts` is consulted by the planner (`stages/graph.py` folds a SITE MAP into the plan `orient`) and scopes by metadata `filter` (`site $eq <host>`), gated by `CLARION_SITE_KNOWLEDGE=1`, fail-open. **Per-category-not-per-site** (research: `docs/research/moss-index-design.md`; Moss `QueryOptions.filter`). Proven on usa.gov (consult surfaces `/complaints` first; non-matching site filter → 0) |
| **User memory: facts · preferences · workflow episodes** (knowledge layer b+c) | **✅ REAL — Moss-backed, behind `CLARION_MEMORY=1`** | `retrieval/memory_moss.py` (one `clarion-mem-{user}` index, `kind`-discriminated docs); `Memory.recall` warm-starts the plan (`stages/graph.py` planner → `prior_plan_hint`); `gov_proof` writes a finished run as a `WorkflowEpisode`; `app/remember.py` = consent-gated "remember?" capture (secrets never offered → *no memory without a yes*). **Firewall:** `Recall` has no `source_node_id` → structurally unspeakable, re-grounded live. **Pending:** voice remember-gate surfacing (`voice_entry`) + a live episode round-trip. Spec: `docs/clarion-memory-design.md`. NB Moss metadata `filter` (per the structure-index research) could later fold this to one shared `clarion-mem` index keyed by `user_id` |

---

## Done this session — the Clarion-PE/G migration (commits on `feat/clarion-extension`)

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
5. **Data-model simplification pass.** Audit `ClarionState`/`_PlanState` + value
   objects; keep only what we track (no bloat).

---

## Points to FIX / TEST

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
