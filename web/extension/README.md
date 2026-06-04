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

- `manifest.json` — MV3 manifest (debugger/commands/offscreen/alarms permissions).
- `service-worker.js` — the background module: shortcut → attach → relay bridge.
- `relay-client.js` — pure, chrome-free framing functions (unit-tested under node).
- `test/relay-framing.test.mjs` — round-trips the framing against the frozen wire.
- `test/relay-interop.mjs` — optional live check against a running Python relay.

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

## Notes

- The relay is 1:1 with a tab; re-running the shortcut tears down the prior
  session first.
- A `chrome.alarms` ~20s tick is a keepalive backstop, and the WebSocket
  reconnects with backoff if the relay drops.
- Browser-side voice (an offscreen LiveKit client) is a **separate** feature —
  `service-worker.js` leaves a marked extension point for it and does not build it.
