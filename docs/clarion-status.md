# Clarion — LIVE STATUS (read this first each session)

_Last updated: 2026-06-04 · branch `feat/clarion-extension` · supersedes
`docs/clarion-extension-handoff.md` (that said "voice is the open bug" — no longer true)._

This is the single source of truth for **where we are and what's left**. Keep it
current: when you finish or change something, edit this file in the same commit.

---

## TL;DR (the one paragraph)

The **voice plane + perception + actuator + the kernel ENGINE are REAL and proven
live** on a real tab. The thing that made it feel like a stub was the **planning
layer**, hardcoded to "pay my electric bill" at 3 layers. This session shipped the
missing **goal-formation on-ramp**: `read_screen` (ORIENT) reads back what's
actually on the live page, grounded to real AX nodes, and the goal now comes from
the user's confirmed intent — never a baked string. Verified live: the LLM called
`read_screen` on usa.gov through the extension relay. **Still hardcoded (next
phase):** the task PLAN + the GROUND facts (`HeroRetriever` fixture). 94 tests green.

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
| **GROUND facts (page values)** | **❌ FIXTURE constants** | `app/runtime.py:52` `HeroRetriever._HERO_FACTS` ($84.32 etc.) |
| Retrieval (Moss/Gemini embeddings, KB) | **REAL** | `clarion-kb` index, `retrieval/` |
| User profile/traits store | **port exists, unused** | `Memory`/`Profile` ports |

---

## Done this session (commits on `feat/clarion-extension`)

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

1. **Page-grounded GROUND (kill the fixture).** Replace `HeroRetriever._HERO_FACTS`
   with a `PageRetriever(actuator)` so the kernel's GROUND reads real page facts
   (each grounded to a real `node_id`), not constants. Wire it in `runtime.py`.
   **Verify on the demo pay tab** (don't break the consent demo unseen).
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

- [ ] **Hear the `read_screen` readout voiced** — blocked today by Gemini **TTS 429
      per-minute rate limit** (recoverable). Re-run when quota cools; confirm the
      agent speaks the grounded readout.
- [ ] End-to-end task on the **demo pay tab**: orient → confirm "pay my electric
      bill" → grounded readback ($amount/payee/due) → **PAY hard-stop at consent** →
      approve → confirm. (The hero acceptance; needs the pay tab attached, not usa.gov.)
- [ ] After #1/#2 above: prove the SAME flow runs from a user-stated goal on a
      non-pay page, and **degrades honestly** ("I can't complete that here").
- [ ] Keep `pytest clarion -q` green (currently **94**, 6 deselected live-Moss).
- [ ] `python scripts/copy_lint.py <file>` on any new copy (no "assistant/helper/assist").

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
