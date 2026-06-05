# Clarion — LIVE STATUS (read this first each session)

_Last updated: 2026-06-04 · branch `feat/clarion-extension` · supersedes
`docs/clarion-extension-handoff.md` (that said "voice is the open bug" — no longer true)._

This is the single source of truth for **where we are and what's left**. Keep it
current: when you finish or change something, edit this file in the same commit.

---

## TL;DR (the one paragraph)

The **voice plane + perception + actuator + the kernel ENGINE are REAL and proven
live** on a real tab. The thing that made it feel like a stub was the **planning
layer**, hardcoded to "pay my electric bill" at 3 layers. A prior session shipped
the **goal-formation on-ramp** (`read_screen`/ORIENT, goal from confirmed intent).
**This session killed the GROUND fixture (Gap 1):** the kernel now grounds on the
**real page** via `PageRetriever(actuator)` → `extract_text_facts` over the live AX
tree — every fact sourced to a real AX `nodeId`, no `$84.32` constant. Proven on
TWO real public sites (usa.gov, wikipedia): grounded facts carry real node ids and
the fixture's `$84.32/Northwind` never leaks; honest absence when the page lacks a
value. **Testing rule (locked):** real sites only — NOT the `web/demo-site` clone.
**Still hardcoded (next phase):** the task PLAN (pay topology). 99 tests green.

---

## Real vs stub inventory (the honest map)

| Piece | State | Evidence / location |
|---|---|---|
| Voice: LiveKit dispatch · Deepgram STT · Gemini LLM · Gemini TTS | **REAL, live** | `app/voice_entry.py`; worker log shows greet + transcripts + tool calls |
| Perception (CDP AXTree → numbered map) | **REAL** | `actuator/pipeline.py`, `actuator/*actuator.py` |
| Actuator act (click/fill/navigate over CDP) | **REAL** | `Input.dispatchMouseEvent`, native-setter fills |
| Kernel loop GROUND→…→CONFIRM | **REAL (calls real ports)** | `kernel/graph.py:141/339/359` |
| **ORIENT `read_screen` (grounded page readout)** | **REAL, NEW, live-verified** | `read_screen` tool + `summarize_ax_tree`/`describe_page` |
| Goal source | **REAL (from confirmed user intent)** | `voice_entry.py` `set_goal`; no baked default |
| **Task PLAN / stage topology** | **❌ HARDCODED to pay flow** | `stages/planner.py:90` (ignores goal), `stages/graph.py:171` |
| **GROUND facts (page values)** | **✅ REAL (page-grounded)** | `app/page_retriever.py` `PageRetriever`; `actuator/pipeline.py` `extract_text_facts`; wired `app/runtime.py`. `HeroRetriever` kept only as a test double. |
| Retrieval (Moss/Gemini embeddings, KB) | **REAL** | `clarion-kb` index, `retrieval/` |
| User profile/traits store | **port exists, unused** | `Memory`/`Profile` ports |

---

## Done this session (commits on `feat/clarion-extension`)

- **Latency migration Step 0 + 1 — `perceive()` made cheap (lazy stamping):**
  measured the baseline on the REAL `usa.gov/benefits` tab over the live extension
  transport (broker, 45 interactive nodes): the per-node stamp loop was confirmed
  dominant — `perceive_ms` **~297ms cold / ~100ms warm** with **90 stamp
  round-trips** (2 per node: `DOM.pushNodesByBackendIdsToFrontend` +
  `DOM.setAttributeValue`); attribution showed the stamp loop at ~2–10ms/node vs a
  ~32ms triple-fetch. **Step 1:** `perceive()` now stamps ZERO nodes — it records
  `index -> backend_id` and stamps the single target node lazily on first
  `act`/`read`/`read_value` (`_ensure_stamped`); added a target-node-only
  `reperceive_node` (shared `_NODE_STATE_JS`). After: **~38ms cold / ~34ms warm,
  0 stamp round-trips** → 90→0 stamp round-trips (total perceive round-trips 93→3,
  ~31×), ~7.8× cold / 2.7× warm wall-clock. Applied to BOTH transports (parity
  green); instrument facility = `instrument/timed.py::Timed`; `perceive_ms` lands
  in `/tmp/clarion-worker.log` via a `[lat]` line. 100 tests green.
- **Gap 1 — page-grounded GROUND (killed the fixture):** `extract_text_facts`
  (pure harvest of grounded StaticText/heading over the live AX tree, real nodeids,
  InlineTextBox/ignored filtered) in `actuator/pipeline.py`; `read_facts()` on both
  actuators (Playwright + extension, shared); `PageRetriever` (Actuator→Retriever,
  goal-ranked, value-bonus, honest absence) in `app/page_retriever.py`; wired
  `runtime.py` to `TimedRetriever(PageRetriever(actuator))`. `HeroRetriever` kept as
  a test double only. +5 tests (`app/tests/test_page_retriever.py`) → **99 green**.
  Live-proven on real sites (usa.gov, wikipedia). Testing-target rule locked: real
  sites only, never the demo clone.

### From the prior session
- `feat(voice): real grounded ORIENT (read_screen) + un-hardcode the goal` — `read_screen`
  tool, `PageReadout`, pure `summarize_ax_tree`/`readout_from_selector_map`,
  `describe_page()` on both actuators, goal from confirmed intent, honest terminal
  line, ORIENT→confirm→ACT instructions.
- `polish(orient): singular/plural counts + de-dup group-phrase logic`.
- Live-verified: LLM called `{"function":"read_screen"}` on the real usa.gov tab via
  the broker relay → `describe_page` ran `Accessibility.getFullAXTree` → grounded
  readout (`tools execution completed`). Readout content proven correct on the demo
  page + usa.gov via `PlaywrightActuator.describe_page` (every item grounded).

---

## REMAINING / leftover functionalities (the next-phase backlog, ordered)

1. ~~**Page-grounded GROUND (kill the fixture).**~~ ✅ **DONE this session.**
   `PageRetriever(actuator)` reads the real page (`extract_text_facts` over the live
   AX tree, every fact sourced to a real `nodeId`); wired in `runtime.py`
   (`TimedRetriever(PageRetriever(actuator))`). Proven on real sites (usa.gov,
   wikipedia). ⏭ Open quality follow-on (belongs to Gap 2/3, not grounding):
   the ranker surfaces long paragraphs / nav noise — PROPOSE needs label↔value
   pairing + crisp value extraction for a clean spoken readback.
2. **Real goal-conditioned planner.** `stages/planner.py:plan_goal(goal)` must stop
   ignoring `goal` and emit a plan from goal + page (the documented LLM-planner seam).
   `stages/graph.py:build_stage_graph` bakes the pay topology at line 171 — make the
   plan/topology derive from the goal (per-goal rebuild, or a generic stage executor).
   ⚠️ `stages/tests/test_stages.py` pins the hardcoded hero plan AS SPEC — that rewrite
   must redo those tests.
3. **Knowledge layer** (the user's vision): graphs + embedding DBs over
   **(a) website functionalities** (seed = `PageReadout.affordances`),
   **(b) task paths** (the LangGraph plans we run), **(c) user profile/traits**
   (the `Memory`/`Profile` port). Categorize + persist + reuse across sites.
4. **Data-model simplification pass.** Audit `ClarionState` + value objects; keep only
   what we actually track in state (the user's standing instruction: no bloat).

---

## Points to FIX / TEST before "works end-to-end"

- [x] **Gap 1 grounding proven on REAL sites** (usa.gov, wikipedia) via the real
      `PlaywrightActuator` + `PageRetriever`: facts sourced to real AX nodeids,
      fixture `$84.32/Northwind` never leaks, honest absence on value-less pages.
- [ ] **Product-path proof (extension on the user's REAL tab):** worker restarted
      with the Gap-1 code; point the extension Chrome at a real account/bill page,
      press the shortcut, state a goal → confirm the worker log shows GROUND
      grounding that page's real facts (no fixture). One human step = the shortcut.
- [ ] **Hear the readout voiced** — gated only by Gemini **TTS 429** per-minute
      limit (recoverable); the tool/GROUND path runs regardless of audio.
- [ ] **Spoken-readback quality** (Gap 2/3): label↔value pairing + crisp value
      extraction in PROPOSE so GROUND speaks "Amount due $84.32", not a paragraph.
- [ ] Keep `pytest clarion -q` green (currently **99**, 6 deselected live-Moss).
- [ ] `python scripts/copy_lint.py <file>` on any new copy (no "assistant/helper/assist").

**Testing rule (LOCKED 2026-06-04):** never test on the `web/demo-site` clone —
only ACTUAL real sites (the extension drives the user's real tab). Acceptance =
grounded readback on a real page + honest decline; NOT a completed payment.

---

## How to run + LOGS (better log maintenance)

```bash
scripts/clarion-up.sh                 # rotates logs → .prev, starts logsink+broker+worker, opens Chrome on usa.gov/benefits
scripts/clarion-up.sh http://localhost:8770/account/pay   # … on the demo PAY tab (hero acceptance)
scripts/clarion-status.sh             # ONE command: ports + procs + tail of every log (run this first to see state)
scripts/clarion-down.sh               # stop everything (reaps the worker's whole job tree)
```

**Logs** (rotated to `*.prev` on each `clarion-up`, so a session never reads stale lines):
- `/tmp/clarion-worker.log` — agent worker; phases tagged `[loop]`, sim tagged `[SIM]`, tools `executing tool`.
- `/tmp/clarion-broker.log` — always-on relay broker (8771 ext / 8773 agent); extension/agent connect + session.start cache/replay.
- `/tmp/clarion-ext.log` — browser SW + HUD via the sink (`scripts/clarion-logsink.py`).

**Restarting ONLY the worker (to load worker-side code changes without touching Chrome/the extension):**
- Reap first: `pkill -if "clarion.app.voice_entry"; pkill -if "from multiprocessing.spawn"` (orphan job subprocs steal dispatches).
- Start detached so it survives the shell (a bare `nohup &` from a tool call gets reaped):
  `CLARION_ACTUATOR=extension nohup .venv/bin/python -m clarion.app.voice_entry dev >>/tmp/clarion-worker.log 2>&1 &` inside a `( … )` subshell, or the harness's background mode.

**Operational gotchas (cost real time before — see project memory):**
- Same-profile Chrome relaunch does NOT reload the extension → prove fresh SW code by a NEW line in `/tmp/clarion-ext.log`.
- Killing a job leaves the LiveKit room's agent slot occupied → no re-dispatch. **Delete the room** to force a clean dispatch:
  `api.LiveKitAPI(...).room.delete_room(api.DeleteRoomRequest(room="clarion-hero"))` (creds from `agent/.env`).
- Autonomous real-sim (no mic): arm worker `CLARION_SIM_UTTERANCES="what is on this page"`, then `scripts/clarion-sim.py 90` joins the room → dispatch. Text stands in for mic→STT.
- Gemini AI-Studio TTS ~100 req/min → `429 RESOURCE_EXHAUSTED` under load; tool calls still run (verify from `executing tool`, not from audio).

---

## Acceptance for "the whole thing works end-to-end"

1. `clarion-up.sh` → press shortcut → `read_screen` reads back the real page (heard).
2. State a goal → agent confirms it → drives the task with **per-step consent** and a
   **hard-stop at the irreversible step**, reading **page-grounded** facts (not fixtures).
3. On a page that doesn't afford the goal, it says so honestly (no fake "task complete").
4. `pytest clarion -q` green.
