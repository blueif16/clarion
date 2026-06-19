# Clarion — next-session kickoff prompt (paste this)

> Copy everything in the box below to start the next session.

---

Continue the Clarion work on branch `feat/clarion-extension`.

**Read first, in full:** `docs/clarion-status.md` (the LIVE status — what's real, what's
left, what to test), then `docs/clarion-architecture.md` (the Clarion-PE/G design, marked
SHIPPED), then `docs/foundation.md` §3 (the product = task-aware, NOT a generic describer),
then project memory (`clarion-architecture-and-direction`, `clarion-runtime-and-logs`,
`no-fakes-and-direct-logs`, `chrome-debugger-devtools-conflict`,
`gemini-structured-output-thinking-latency`).

**Where we are (verified 2026-06-05):** the de-hardcoding is SHIPPED. The "pay my electric
bill" topology is deleted; a generic LLM (`GeminiReasoner`, `thinking_budget=0`, ~2s, behind
the frozen `Reasoner` port) plans the goal and decides each grounded step, and the kernel
enforces the two invariants in code. All four systems landed (Reasoner · PairedFact +
membership/pairing fences · dual-signal irreversibility gate + NegativeVerifier · generic
anchored done-check). **Proven AUTONOMOUSLY end-to-end on two real gov sites, zero
site-specific code** (`app/gov_proof.py`): usa.gov (read-only, grounded + cited +
anchor-certified) and weather.gov (form filled, submit → UNKNOWN → consent hard-stop →
declined, never submitted). 178 tests green + a goal-agnostic invariant spec (red-before-green
proven by mutation). 12 commits `ec8a265`→`d22faf7`.

**YOUR MANDATE THIS SESSION: actually TEST whether it works — drive the real system, don't
just trust the green suite.** The autonomous Playwright proof passed; what is NOT yet proven is
the **LIVE-VOICE / extension product path on a real tab**. So:
1. **Live-voice end-to-end on a real gov tab.** `scripts/clarion-up.sh` → press the shortcut →
   hear the ORIENT readback → speak a goal → confirm it → watch it drive the de-hardcoded task
   plane with **per-step consent** and the **irreversible hard-stop**, reading page-grounded
   facts with citations. ONE human step = the shortcut + speaking. Drive it yourself; read the
   log FILES (`/tmp/clarion-worker.log`), never make the owner copy-paste.
   - **Expect ~2s think-gaps per step** (Step-6 speculation isn't built yet) — that's known, not
     a bug. If Gemini 503s, the `ResilientReasoner` fails over to Qwen/Nebius.
   - Watch for the real failure modes: does the gate hard-stop fire *audibly* before an
     irreversible click? does it speak only grounded/paired values (no hallucinated number)?
     does it honestly decline / hedge when the page doesn't afford the goal?
2. **Stress the invariants on messy real sites** — a real form, a benefits portal, a page with
   an ambiguous label↔value table. Confirm: no ungrounded/mispaired value is ever spoken, an
   uncovered negative hedges, every consequential step gates.

**Open items (after the live test, ordered — see `docs/clarion-status.md` → REMAINING):**
1. **Step-6 SpeculationController + DeliveryGate** — hide the ~2s decode under the spoken turn
   (pre-fire on partial STT; DeliveryGate re-checks the target node between "yes" and act).
   Needed for the <800ms LIVE voice loop. `actuator/reperceive_node` is already in place.
2. **Actuator AX enrichment for the gate** — surface `type=submit` / `<form>` membership /
   off-origin so `kernel/irreversibility.py::_structural_prescreen` escalates a submit to
   `irreversible` (not just `unknown`). TODO is in that file.
3. **Knowledge layer** (owner's vision): graphs + embedding DBs over website functionalities
   (seed = `PageReadout.affordances`), task paths (the subgoal plans), user profile/traits
   (the `Memory`/`Profile` ports).
4. **Data-model simplification pass** — audit `ClarionState`/`_PlanState`, keep only what we track.
5. **Rotate the `NEBIUS_API_KEY`** — it was pasted in chat last session; treat as compromised.

**Standing rules from the owner (do not violate):** NEVER hardcode anything — the goal is set
only after the user states + confirms it. NO fixtures — every function real; drive the real
system and read the log FILES (never make me the tester / copy-paste). NEVER swap models to fix
latency — config/pipeline/parallelize first (the `thinking_budget=0` win proved this). Backend
stays LangGraph; data models stay simplified. Words "assistant/helper/assist" are banned in any
copy (`python scripts/copy_lint.py <file>`). Verify external methods against **Context7**
(resolve-library-id → get-library-docs) before trusting them.

**Run / verify loop:**
- `scripts/clarion-status.sh` — FIRST, to see ports/procs/logs at a glance.
- `scripts/clarion-up.sh [URL]` — starts the stack (rotates logs → `.prev`); no arg = pay.gov/public/home (demo landing).
- `cd agent && .venv/bin/python -m clarion.app.gov_proof` — the AUTONOMOUS de-hardcoded proof (no voice).
- Gate: `cd agent && .venv/bin/python -m pytest clarion -q` (keep ≥178 green).
- Gotchas: killing a job leaves the room's agent slot occupied → DELETE the room to re-dispatch;
  same-profile Chrome relaunch does NOT reload the extension (prove fresh SW via a new
  `/tmp/clarion-ext.log` line); `chrome.debugger` attach fails while DevTools is open.

**Keep `docs/clarion-status.md` current** — edit it in the same commit as any change so this
handoff stays true.
