# Clarion extension — hand-off for the next session

Branch: `feat/clarion-extension`. You are continuing the browser-extension + voice work.

## YOUR MANDATE (read first — non-negotiable)
- **Fix every bug. Do not hand back partial work.** The owner is done being the manual tester.
- **You verify, not the human.** Drive the REAL system yourself and read the logs from files. Only involve the human for the *single final* confirmation, and only once you are *certain* it works — never as a debugging loop.
- **No fake tests.** Do NOT prove anything with `relay-interop` canned CDP, `_fake_extension_client`, or `CLARION_DEMO_MODE=1` fixtures. Drive the real stack; automate only the two human actions: the shortcut keystroke (osascript `System Events` → Chrome) and "speech" via text (`session.generate_reply(user_input=...)`).
- **Read logs directly — never ask for copy-paste.** `tail` the files below.
- Be **certain and specific** about how it moves forward before you ask the human anything.

## Logs you can read directly
- Agent side: `/tmp/clarion-worker.log` — every phase tagged `[loop]`.
- Browser side (SW + HUD): `/tmp/clarion-ext.log` — via the always-on sink (`scripts/clarion-logsink.py`, HTTP :8772).
- `tail -f` both. Verify a change by reading these, not by asking the user.

## Run / drive
- Up/down: `scripts/clarion-up.sh` (mints token → `web/extension/config.js`; starts log sink :8772 + voice worker + Chrome+extension), `scripts/clarion-down.sh`.
- Real-sim (no mic, no Chrome): `scripts/clarion-sim.py <hold_s>` joins room `clarion-hero` → triggers real agent dispatch. Arm the worker with `CLARION_SIM_UTTERANCES="pay my electric bill|yes"` to inject text-as-speech.
- Deterministic gate (must stay green): `cd agent && .venv/bin/python -m pytest clarion -q` → 89 passed.

## What was DONE this session (verified)
- **Voice decoupled from the tab relay** in `agent/clarion/app/voice_entry.py`: `AgentSession.start()` + greet happen immediately on dispatch (agent hears the user right away); the tab relay attaches + `StageGraphRunner` binds in a background `attach_tab` task; `advance_task` returns "connecting to your tab" until `runner.ready`. Proven via real-sim: injected "pay my electric bill" → real Gemini `advance_task` tool-call. 89 tests pass.
- **Debug HUD** (`web/extension/hud.js`): toolbar badge + on-page overlay (selectable + ⧉ copy button); wired through `service-worker.js` for attach/relay/voice phases; `manifest.json` gained `action` + `connect-src` for ws://8771 and http://8772.
- **Durable log sink** (`scripts/clarion-logsink.py` + `hud.js sinkLog`), wired into up/down.
- Memory written: `clarion-runtime-and-logs`, `no-fakes-and-direct-logs`, `chrome-debugger-devtools-conflict`.

## THE OPEN BUG (fix this first — it's why the human can't trust the relay)
**The CDP relay (port 8771, the tab bridge) only binds when the voice agent DISPATCHES, and dispatch needs a room participant** (the extension's offscreen, which needs the mic grant). So the backend port stays closed until mic-granted → "relay dropped, reconnecting" → the human is never sure the relay is open. Proven live: 8771 is closed at idle and only binds the instant a participant joins the room.

The relay is the *tab* bridge; it must **not** be gated behind *voice*. Constraint: LiveKit `dev` runs each job in a **subprocess** (verified: job pid ≠ worker pid), so the `ExtensionActuator` and the relay must be co-located in the job — which is *why* the relay got nested there.

**Robust fix to implement (your call on approach, but the port must be ALWAYS-ON):**
- **Option A — relay broker (preferred):** make the relay a standalone always-on server started by `clarion-up` (binds 8771 at boot, independent of voice). The extension connects as today; the agent's `ExtensionActuator` becomes a *client* of the broker (sends CDP over a socket, gets results back). Preserves the FROZEN extension-facing wire (`docs/extension-build.md`, relay protocol v1).
- **Option B — in-process jobs:** run jobs threaded/in-process so a relay bound at worker startup is reachable by the entrypoint's actuator. Smaller if LiveKit supports it cleanly (check docs first).

## Acceptance criteria — you must self-verify ALL of these from the logs before involving the human
1. `scripts/clarion-up.sh` → **8771 is LISTENING immediately**, before any shortcut/mic (the decoupling). Confirm with `lsof -iTCP:8771 -sTCP:LISTEN`.
2. Real-sim: arm `CLARION_SIM_UTTERANCES`, run `clarion-sim.py` → `/tmp/clarion-worker.log` shows `dispatched → AgentSession STARTED → [SIM] 'pay my electric bill' → advance_task tool-call`.
3. With a tab attached (real extension OR `web/demo-site` up on :8770), `advance_task` actually drives the page (not "connecting to your tab"); the PAY step HARD-STOPS at the consent gate; grounded facts carry `source_node_id`.
4. `/tmp/clarion-ext.log` shows the browser loop: `service worker started → debugger attached → relay connected → voice connected`.
5. `pytest clarion -q` stays green (89).
6. Only THEN: ask the human to do the one real-hardware confirmation (reload extension, close DevTools, Cmd+Shift+Y, grant mic, speak) — with you already certain, and watching both logs to narrate it.

## Gotchas (don't relearn these)
- `chrome.debugger.attach` fails while **DevTools is open on the tab** ("attach FAILED — close DevTools"; ⌘⌥I closes it). The page tab's DevTools is not needed — use the HUD + the sink.
- The extension must be **reloaded** (chrome://extensions → ⟳) after any `web/extension/*` change; confirm by seeing `service worker started` land in `/tmp/clarion-ext.log`.
- Relay protocol v1 framing is FROZEN — keep `relay-client.js` / the extension-facing wire intact through any broker refactor.
- Git: scope to `git -C /Users/tk/Desktop/conv-agent`, feature branch, conventional commits, don't push unless asked. Several files are uncommitted (see `git status`); consider committing the decouple + observability as one unit before the broker work.
- Provider state: LiveKit/Deepgram/Gemini are live; `config.js` is minted by `clarion-up`.

## First moves for the next session
1. Read this file + the three memory entries.
2. Implement the always-on relay (Option A or B), update `clarion-up.sh` to start it at boot.
3. Self-verify acceptance criteria 1–5 from the logs. Fix until green.
4. Commit. Then hand the human exactly ONE confirmation step, certain it works.
