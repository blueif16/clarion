# Clarion — next-session kickoff prompt (paste this)

> Copy everything in the box below to start the next session.

---

Continue the Clarion work on branch `feat/clarion-extension`.

**Read first, in full:** `docs/clarion-status.md` (the LIVE status — real vs hardcoded,
what's left, what to fix/test), then `docs/foundation.md` §3 (the product = task-aware,
NOT a generic describer), then project memory (`clarion-architecture-and-direction`,
`clarion-runtime-and-logs`, `no-fakes-and-direct-logs`, `chrome-debugger-devtools-conflict`).

**Where we are (verified 2026-06-04):** Voice + perception + actuator + the kernel engine
are REAL and proven live. The goal-formation on-ramp shipped: `read_screen` (ORIENT) reads
back the live page grounded to real AX nodes, and the goal now comes from the user's
confirmed intent — no baked string. Live-verified: the LLM called `read_screen` on usa.gov
through the extension relay. 94 tests green.

**Standing rules from the owner (do not violate):** NEVER hardcode anything — the goal is
set only after the user states + confirms it; until then Clarion is a grounded screen reader
that reads the page and recommends. NO fixtures — every function real; drive the real system
and read the log FILES (never make me copy-paste, never make me the tester). Backend stays
LangGraph. Data models stay simplified (only what we track in state). Words "assistant/
helper/assist" are banned in any copy.

**Your mandate — the deep un-stub (ordered, in `docs/clarion-status.md` → "REMAINING"):**
1. Kill the GROUND fixture: a `PageRetriever(actuator)` so the kernel grounds real page
   facts (each with a real `node_id`), replacing `HeroRetriever._HERO_FACTS`
   (`app/runtime.py:52`). Verify on the demo PAY tab — don't break the consent demo unseen.
2. Real goal-conditioned planner: `stages/planner.py:plan_goal` must stop ignoring `goal`;
   `stages/graph.py:171` bakes the pay topology. ⚠️ `stages/tests/test_stages.py` pins the
   hardcoded plan AS SPEC — redo those tests as part of the change.
3. The knowledge layer (owner's vision): graphs + embedding DBs over website functionalities
   (seed = `PageReadout.affordances`), task paths (the LangGraph plans), user profile/traits
   (the `Memory`/`Profile` port).

**Verify EVERY external method against Context7** (resolve-library-id → get-library-docs)
before trusting it — langgraph, livekit-agents, livekit-client, google-genai, playwright/CDP.
Call `search_past_bugs` before debugging and `search_references` before integration code.

**Run / verify loop:**
- `scripts/clarion-status.sh` — FIRST, to see ports/procs/logs at a glance.
- `scripts/clarion-up.sh [URL]` — starts the stack (rotates logs → `.prev`). No arg = usa.gov;
  pass `http://localhost:8770/account/pay` for the hero pay flow.
- Logs (rotated per `up`): `/tmp/clarion-worker.log` (`[loop]`/`[SIM]`/`executing tool`),
  `/tmp/clarion-broker.log`, `/tmp/clarion-ext.log`. Read them directly.
- Autonomous real-sim (no mic): arm worker `CLARION_SIM_UTTERANCES="…"`, then
  `scripts/clarion-sim.py 90`. Gotchas: killing a job leaves the room's agent slot occupied →
  DELETE the room to force re-dispatch; Gemini TTS ~100 req/min → 429 under load (tools still
  run — verify from `executing tool`, not audio); same-profile Chrome relaunch does NOT reload
  the extension (prove fresh SW code via a new `/tmp/clarion-ext.log` line).
- Gate: `cd agent && .venv/bin/python -m pytest clarion -q` (keep ≥94 green).

**Acceptance (end-to-end):** `clarion-up` → shortcut → `read_screen` reads the real page
(heard) → state a goal → agent confirms it → drives the task with per-step consent + hard-stop
at the irreversible step, reading PAGE-GROUNDED facts (not fixtures) → honest decline on a page
that can't afford the goal → tests green. Then ONE confirmation to the owner.

**Keep `docs/clarion-status.md` current** — edit it in the same commit as any change so this
handoff stays true.
