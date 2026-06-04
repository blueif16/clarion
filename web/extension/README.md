# Clarion — MV3 Chrome extension (CDP relay)

A loadable-unpacked Manifest V3 extension that bridges your **live tab** to the
Clarion Python brain. A keyboard shortcut attaches `chrome.debugger` to the
active tab and relays its Chrome DevTools Protocol over a local WebSocket so the
Python `ExtensionActuator` can perceive and act on the real page you are looking
at — the co-pilot drives your own authenticated tab, nothing is screen-scraped
out of band.

The extension is a **dumb relay**: it forwards each CDP `method`+`params`
straight to `chrome.debugger.sendCommand` and pipes the result back by `id`. It
never interprets CDP. The wire is Relay protocol v1 (FROZEN) — see
`docs/extension-build.md`.

## Files

- `manifest.json` — MV3 manifest (debugger/commands/offscreen/alarms permissions + CSP).
- `service-worker.js` — the background module: shortcut → attach → relay bridge, plus
  the browser-voice lifecycle (mic grant → offscreen LiveKit doc → leave on session end).
- `relay-client.js` — pure, chrome-free framing functions (unit-tested under node).
- `test/relay-framing.test.mjs` — round-trips the framing against the frozen wire.
- `test/relay-interop.mjs` — optional live check against a running Python relay.
- `offscreen.html` / `offscreen.js` — the long-lived offscreen document that joins the
  LiveKit room, publishes the mic, and plays the agent's audio (see "Voice" below).
- `request-mic.html` / `request-mic.js` — a full extension tab that prompts for the mic
  once (the only context that can).
- `vendor/livekit-client.umd.js` — the VENDORED LiveKit SDK (no remote code; see
  `vendor/README.md`). `vendor/smoke.cjs` parses it and asserts the symbols are present.
- `config.example.js` — template for the gitignored `config.js` (LiveKit URL/room/token).

## 1. Start the Python relay

In the agent venv, stand up the WebSocket server the extension connects to. It
binds `ws://127.0.0.1:8771` and waits for the extension client:

```python
from clarion.actuator.relay import WebSocketCdpRelay
relay = await WebSocketCdpRelay().start()
await relay.wait_connected()
# now relay.send("DOM.getDocument", {"depth": -1, "pierce": True}) drives the tab
```

(In the full app this is owned by `ExtensionActuator`; the snippet above is just
the minimal server to point the extension at.)

## 2. Load the extension unpacked

1. Open `chrome://extensions/`.
2. Toggle **Developer mode** on (top-right).
3. Click **Load unpacked** and select this `web/extension/` directory.

### Suppress the debugger banner

`chrome.debugger` normally shows a yellow "Clarion started debugging this
browser" banner. Launch Chrome with the flag below to silence it for a clean
demo (quit Chrome fully first):

```bash
# macOS
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --silent-debugger-extension-api

# Linux
google-chrome --silent-debugger-extension-api

# Windows
chrome.exe --silent-debugger-extension-api
```

## 3. Trigger it

Focus the tab you want Clarion to operate on, then press the shortcut:

- **macOS:** `Command+Shift+Y`
- **Windows / Linux:** `Ctrl+Shift+Y`

The service worker attaches the debugger to the active tab, enables the CDP
domains (`DOM`, `Accessibility`, `DOMSnapshot`, `Runtime`, `Page`), opens the
WebSocket to the relay, and sends `session.start` with the tab's id, url, and
title. From there every `cdp` request from Python is forwarded to the tab and
its result is returned by `id`.

Press the shortcut again on a different tab to move the session; closing the tab
or detaching the debugger ends the session cleanly (`session.end`).

### Shortcut not firing?

If the suggested key collides with another extension, set it manually at
`chrome://extensions/shortcuts` — find **Clarion → "Attach Clarion to the active
tab and bridge it to the local relay"** and assign a key.

## 4. Verify the framing

No browser needed — the wire framing is covered by node's built-in test runner:

```bash
node --test web/extension/test/
```

Optional live interop against a running Python relay (needs `npm install` for the
`ws` devDependency):

```bash
cd web/extension && npm install
node test/relay-interop.mjs    # answers each cdp request with a canned result
```

## 5. Voice in the browser

The blind user talks to Clarion through their own browser. Voice rides a
**separate** LiveKit/WebRTC connection from the CDP relay above — the relay WS to
`ws://127.0.0.1:8771` is unchanged and stays in the service worker. The voice
connection lives in an **offscreen document** (`offscreen.html`) because MV3
service workers are evicted at ~30s idle and would drop a long-lived WebRTC
connection; offscreen documents are exempt.

```
Extension (your Chrome)                         Python brain (unchanged)
  service-worker  ── CDP relay WS (8771) ──────  ExtensionActuator
  offscreen.js    ── LiveKit room (wss) ───────  voice_entry worker (the agent)
```

The offscreen document joins the SAME LiveKit room the unchanged
`clarion.app.voice_entry` worker is on, publishes your microphone, and plays the
agent's incoming audio. It uses the **vendored** `livekit-client` (no remote
code — see `vendor/README.md`).

### Mic permission comes first

`getUserMedia()` cannot prompt for the mic from an offscreen document, side
panel, or popup — only a top-level extension page can. So on session start, if
the grant is not already in place, the worker opens `request-mic.html` (a full
extension tab) which prompts once; Chrome then remembers the grant for the
extension origin, and the offscreen document opens the mic without prompting.

### Configure the room

Voice is opt-in: it only starts if `config.js` exists. Copy the template and fill
it in (the real file is gitignored — never commit a token):

```bash
cp web/extension/config.example.js web/extension/config.js
```

`config.js` does `export default { LIVEKIT_URL, ROOM_NAME, TOKEN }`:

- `LIVEKIT_URL` — your LiveKit signalling URL (`wss://…`), the same project the
  Python worker uses (`LIVEKIT_URL` in `agent/.env`).
- `ROOM_NAME` — the room the human joins. It MUST be the room the Python
  `voice_entry` worker is dispatched into so the agent and the human meet. With
  `python -m clarion.app.voice_entry dev` the worker joins the room named by the
  LiveKit dispatch; in console/explicit-room runs set it to match.
- `TOKEN` — a LiveKit access token for the **human** participant, scoped to
  `ROOM_NAME` with `canPublish` + `canSubscribe`. Tokens expire; regenerate when
  joining.

### Mint a participant token

Use the LiveKit CLI with the API key/secret from `agent/.env`
(`LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET`):

```bash
lk token create \
  --api-key "$LIVEKIT_API_KEY" --api-secret "$LIVEKIT_API_SECRET" \
  --identity human --room "clarion-hero" \
  --join --valid-for 4h
```

Paste the printed JWT into `config.js` as `TOKEN` (and use the same room name as
`ROOM_NAME`). The Python worker mints its own agent-side token from the same
key/secret, so both land in the same room.

### What is proven where

Construction + wiring are real and checked headlessly (manifest, `node --check`,
the vendored-SDK smoke). The live spoken round-trip (real mic in, agent TTS out)
can only be proven with a microphone and a live room — that is integration step
#6, not this feature.

## Notes

- The relay is 1:1 with a tab; re-running the shortcut tears down the prior
  session first.
- A `chrome.alarms` ~20s tick is a keepalive backstop, and the WebSocket
  reconnects with backoff if the relay drops.
- Browser voice starts and stops with the session: it joins the room on
  `session.start` and leaves + closes the offscreen document on `session.end`
  (debugger detach or tab close). It is additive — if `config.js` is absent or
  the mic is denied, the CDP relay still works.
