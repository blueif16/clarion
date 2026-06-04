# Clarion extension — hand-off (voice is the open bug)

Branch: `feat/clarion-extension`. The **relay-coupling bug is FIXED and verified**; the
**browser-voice path does nothing** and is the next session's job. Read this in full,
then the three memory entries (`clarion-runtime-and-logs`, `no-fakes-and-direct-logs`,
`chrome-debugger-devtools-conflict`).

## YOUR MANDATE (non-negotiable)
- **Make voice actually work, end to end, on the real Mac.** The owner is done being the
  manual tester and done with back-and-forth. Drive the real system yourself; read the
  logs from files; only bring the owner in for the single mic-grant + speech, and only
  once you are CERTAIN the rest works.
- **You will be on the Mac with the mic available.** "Open up the Mac and have the voice
  input channels ready" — confirm the OS/Chrome mic permission for the dedicated Clarion
  Chrome profile BEFORE testing, so a denied mic isn't a false negative.
- **Check EVERY method against Context7 docs before trusting it.** Use Context7 MCP
  (`resolve-library-id` → `get-library-docs`) for: `livekit-client` (browser SDK —
  `Room.connect`, `setMicrophoneEnabled`, track subscription/playback), `livekit-agents`
  (Python worker — `AgentSession`, STT/LLM/TTS plumbing, `RoomInputOptions`), and the
  Chrome MV3 APIs (`chrome.offscreen`, `navigator.permissions.query` in a service worker,
  dynamic `import()` support in MV3 SWs, `getUserMedia` from an offscreen document). Don't
  assume an API shape — verify it. (This is how the `confirm_consent` bug below was found:
  `RunContext.disallow_interruptions()` is a plain call in livekit-agents 1.5.x, not a
  context manager.)
- **No fakes.** Drive the real stack; automate only the human actions. NOTE: in the prior
  session's environment, osascript `System Events` keystrokes TIMED OUT (-1712, no
  Accessibility grant) — so `Cmd+Shift+Y` could NOT be injected. If that's still true,
  the shortcut press is a human action; verify everything else first. The agent→broker→tab
  pipe is verifiable WITHOUT the keystroke via an honest Playwright-Chromium "extension
  stand-in" (see `/tmp/clarion_ext_standin.py` — real DOM, real CDP, not canned).

## Logs you read directly (never copy-paste)
- `/tmp/clarion-worker.log` — agent worker; phases tagged `[loop]`.
- `/tmp/clarion-broker.log` — the always-on relay broker (ext/agent connects, session.start cache/replay).
- `/tmp/clarion-ext.log` — browser SW + HUD via the sink (`scripts/clarion-logsink.py`).

## What is DONE + VERIFIED this session (don't redo)
- **Always-on relay broker** (`agent/clarion/actuator/relay_broker.py`) fixes the relay
  coupling: it binds **8771** (extension, FROZEN v1 wire) + **8773** (agent) at BOOT,
  independent of voice/dispatch/mic. The worker's `ExtensionActuator` is now a CLIENT of
  the broker (`relay.BrokerCdpRelay`) and never binds the port. Broker caches + replays
  `session.start` to a late-joining agent. `clarion-up` starts it first and proves 8771
  LISTENING. VERIFIED: 8771 up immediately post-up; real-sim → dispatch → AgentSession
  started → advance_task tool-call; with a real Chromium tab bridged through the broker,
  advance_task perceived + clicked + filled the live page and PAY hard-stopped at consent.
- **`confirm_consent` fix** (`voice_entry.py`): `context.disallow_interruptions()` is a
  plain call now (was a broken `with` that raised TypeError on the consent→act resume).
- **clarion-down reaps the worker's whole job-subprocess tree + orphan sweep.** LiveKit
  jobs run as `python -c from multiprocessing.spawn` (argv lacks "voice_entry"); they
  leaked, stayed registered, and STOLE dispatches (made a sim land on a stale job). Fixed.
- **Relay attach CONFIRMED with the REAL extension** (owner pressed the shortcut twice):
  `service worker started → debugger attached → CDP domains enabled → relay connected ✓
  perceiving` on https://www.usa.gov/benefits. The tab bridge is solid.
- Commits: `df023be` (decouple + observability), `3a7438b` (broker), `bb3be0c`
  (down reap), `9998333` (voice observability + robust config). `pytest clarion -q` = 89.

## THE OPEN BUG — browser voice does NOTHING
Symptom: owner presses `Cmd+Shift+Y` → relay attaches fine, but **no greeting is heard and
speaking produces no response** ("no reflection"). No `request-mic` tab opens.

Evidence (from `/tmp/clarion-ext.log`, owner pressed at 16:04:28):
```
16:04:28 service worker started ... → debugger attached → CDP domains enabled → relay connected ✓ perceiving
```
…and then **NOTHING** — even though `startVoice` was instrumented to log `voice: starting`
as its first line, and the offscreen reports `voice ready/connecting/connected/error`.
`/tmp/clarion-broker.log` shows `extension connected` + `session.start cached` but **never
`agent connected`** → no job ever dispatched for voice → the offscreen never joined the room.

### PRIME SUSPECT (rule out FIRST): the extension is running STALE code
`clarion-up` relaunches Chrome with the SAME `--user-data-dir=/tmp/clarion-chrome-profile`.
If a Chrome instance on that profile is already alive, the new invocation just focuses it
and does **NOT** reload the extension from disk — so SW code edits silently don't take.
"service worker started" can also appear from a normal SW eviction/wake of the OLD code, so
it does NOT prove the new code loaded.
- **Fix the test loop:** fully quit the Clarion Chrome (`scripts/clarion-down.sh` kills the
  profile instance) and confirm it's gone BEFORE `clarion-up`; OR reload at
  `chrome://extensions` (⟳). Then PROVE the new code is live by pressing the shortcut and
  seeing the NEW `voice: starting` line in `/tmp/clarion-ext.log`. Until that line appears,
  you are debugging old code.

### Once on confirmed-fresh code, walk the voice gates (now instrumented)
`startVoice` (web/extension/service-worker.js) logs each gate to the sink:
`voice: starting → voice: config loaded → voice: mic granted → voice: offscreen ready —
sending CONNECT`, and the offscreen logs `voice ready/connecting/connected/error`. Find the
FIRST gate that's missing and fix that:
1. **config** — `loadVoiceConfig` now tries dynamic `import()` then falls back to
   `fetch()`+parse of `config.js`. If `voice: NO config.js` appears, check `config.js`
   exists + the fetch works in the SW (verify MV3 SW dynamic-import support via Context7).
2. **mic** — `micAlreadyGranted()` uses `navigator.permissions.query({name:"microphone"})`
   IN THE SERVICE WORKER. Verify that's reliable in an MV3 SW (Context7 / MDN); it may
   throw or mis-report. If not granted, `request-mic.html` (a full tab) must open and the
   owner clicks Allow. Confirm the OS mic permission for the Clarion Chrome profile too.
3. **offscreen** — `chrome.offscreen.createDocument` + `offscreen.js` loads the vendored
   `vendor/livekit-client.umd.js` (`globalThis.LivekitClient`). If `voice error: vendored
   livekit-client not loaded`, the vendor bundle path/CSP is wrong. Then `Room.connect(url,
   token)` + `setMicrophoneEnabled(true)` — verify both against Context7 `livekit-client`.
4. **dispatch** — once the offscreen joins room `clarion-hero`, the worker dispatches and
   greets (the offscreen plays it — no mic needed to HEAR the greeting). If you hear the
   greeting, voice-out works; then the mic enables voice-in.

### Also confirmed-flaky: LiveKit cloud connectivity
The worker logged a storm of `Cannot connect to host aaa-lqsava47.livekit.cloud:443`
(status 1006) and went silent/stale for ~1h; a worker restart fixed it. If the offscreen
joins but no agent appears, the worker's LiveKit link may be stale — restart the worker and
re-check (the host was reachable via curl 200 even while the worker's WS was wedged).

## Run / drive
- Up/down: `scripts/clarion-up.sh` (NO arg → opens https://www.usa.gov/benefits, the real
  page the owner wants for probing) and `scripts/clarion-down.sh`. Do NOT pass the demo URL.
- Real-sim (no mic/Chrome): `CLARION_SIM_GAP=4 scripts/clarion-sim.py 80` joins the room →
  real dispatch; arm the worker with `CLARION_SIM_UTTERANCES="pay my electric bill|yes"`.
  Use a LONG hold (≥60s) — first dispatch is cold (~12s) and LiveKit closes the session
  when the sim participant leaves (`close_on_disconnect`).
- Real browser pipe WITHOUT the keystroke: `agent/.venv/bin/python /tmp/clarion_ext_standin.py`
  (real Chromium bridged to the broker) — proves agent→broker→tab perceive/act/consent.
- Gate (must stay green): `cd agent && .venv/bin/python -m pytest clarion -q` → 89 passed.

## Current machine state (left running for you)
- broker 8771/8773 up, log sink 8772 up, worker registered, Chrome on usa.gov/benefits,
  demo-site on :8770. `lsof -iTCP:8771 -sTCP:LISTEN` to confirm.

## Acceptance for voice (self-verify from logs before involving the owner)
1. On confirmed-fresh extension code, pressing the shortcut prints the full `voice: …` gate
   sequence in `/tmp/clarion-ext.log` (no silent bail).
2. The offscreen joins room `clarion-hero` → `/tmp/clarion-broker.log` shows the voice job's
   agent activity / the worker logs `[loop] dispatched`, and the agent GREETS (owner hears it).
3. Mic granted → owner speaks → worker logs `advance_task` and the agent reads back grounded,
   sourced facts; PAY hard-stops at consent.
4. `pytest clarion -q` stays green (89).
5. THEN the single owner step: press shortcut, grant mic, speak — with you certain + watching.
