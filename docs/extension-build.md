# Clarion — Chrome-Extension Build (progress log)

_Started 2026-06-04. The real product form: Clarion ships as a **local-unpacked MV3
Chrome extension** that runs in the user's **own authenticated tab**. Verified
viable end-to-end (Claude for Chrome ships the identical `chrome.debugger`/CDP-in-
session pattern in production). Append one line per verified feature; don't bloat._

## Form
Keyboard shortcut → `chrome.debugger.attach` on the user's live tab → CDP triple-fetch →
the **shared §4 pipeline** (`actuator/pipeline.py`, pure Python) → act → re-perceive;
**voice in the browser** via an offscreen-document LiveKit client joined to the same
room the unchanged `voice_entry` worker is on. Kernel / stages / Moss / voice plane are
**unchanged** — the extension is a new `Actuator` transport behind the frozen port.

```
Extension (user's Chrome)                Python brain (unchanged)
  commands → debugger.attach  ── CDP relay (WS) ──  ExtensionActuator(Actuator)
  offscreen @livekit/client  ── LiveKit room ────  voice_entry worker
```

## Relay protocol v1 — FROZEN (the Python↔extension wire)
WebSocket, JSON text frames, request/reply correlated by integer `id`. **Python is the
server** (`ws://127.0.0.1:8771`), the extension service-worker is the client. The
extension is a *dumb relay* — it forwards `method`+`params` to
`chrome.debugger.sendCommand({tabId}, method, params)` and does not interpret them.

- **Python → ext (command):** `{"id": <int>, "type": "cdp", "method": "<Domain.cmd>", "params": {…}}`
- **ext → Python (reply):** `{"id": <int>, "type": "cdp.result", "result": {…}}`
  or `{"id": <int>, "type": "cdp.error", "error": "<msg>"}`
- **ext → Python (lifecycle):** `{"type":"session.start","tabId":<int>,"url":"…","title":"…"}`
  on shortcut+attach; `{"type":"session.end","reason":"…"}` on detach/close.
- **ext → Python (CDP event, optional):** `{"type":"cdp.event","method":"…","params":{…}}`

`ExtensionActuator` issues the same CDP calls as `PlaywrightActuator`:
enable `DOM`/`Accessibility`/`DOMSnapshot`/`Runtime`/`Page`; perceive via
`DOM.getDocument{depth:-1,pierce:true}` + `Accessibility.getFullAXTree` +
`DOMSnapshot.captureSnapshot{includePaintOrder:true,includeDOMRects:true}`; stamp via
`DOM.pushNodesByBackendIdsToFrontend`+`DOM.setAttributeValue`; fill/read via
`Runtime.evaluate` (the shared `_NATIVE_SETTER_JS`/`_READ_JS`); click via
`Input.dispatchMouseEvent` (press+release at bbox center); navigate via `Page.navigate`.

## Status
| # | Feature | Owner | Status | Verified by | Commit |
|---|---|---|---|---|---|
| 2 | Shared §4 pipeline (`pipeline.py`) | orchestrator | ✅ | `pytest clarion` 82/82 | c1a998d |
| 3 | CDP relay + `ExtensionActuator` (Py) | `ext-actuator` | ✅ | parity + act + live-WS, 89/89 | this |
| 4 | MV3 extension shell (shortcut + debugger relay) | `ext-shell` | ✅ | manifest+syntax+framing 14/14+lint; live-load → #6 | this |
| 5 | Voice in browser (offscreen LiveKit) | subagent | ☐ | spoken round-trip | — |
| 6 | Integrate + real gov-portal up-to-the-wall | orchestrator | ☐ | live read-only run | — |

## Log
- 2026-06-04 — **#2** refactor: extracted the pure §4 perception pipeline to
  `actuator/pipeline.py` (shared by both actuator transports); `PlaywrightActuator`
  re-imports the names + keeps a `_containment_filter` shim → zero test changes,
  `pytest clarion` 82 passed / 3 deselected.
- 2026-06-04 — **#3** `ExtensionActuator` + `CdpRelay` (`relay.py`, `extension_actuator.py`):
  the §4 pipeline now runs over the chrome.debugger CDP relay (`WebSocketCdpRelay`
  server + `FakeRelay`). Verified independently: transport **parity** (replayed CDP →
  identical `(index,role,name,bbox)` map), act-correctness (native-setter / dispatchMouse
  / Page.navigate / read), live loopback WS round-trip. `pytest clarion` 89 passed /
  4 deselected; no playwright import; copy-lint clean. (`diff_maps` lifted to `pipeline.py`,
  shared by both actuators.)
- 2026-06-04 — **#4** MV3 extension shell (`web/extension/`): `chrome.commands`
  shortcut → `chrome.debugger.attach("1.3")` → dumb CDP relay (WebSocket client) to the
  Python server; `chrome.alarms` keepalive + WS reconnect; `--silent-debugger-extension-api`
  documented; `// #5 voice` extension point left unbuilt. Verified: manifest valid (6 perms,
  module SW, `Ctrl+Shift+Y`), `node --check` on the modules, framing round-trip 14/14,
  copy-lint clean. Live load-unpacked + the real-tab perceive are deferred to **#6**.
  (`ext-shell` also ran a live interop pass vs the real `WebSocketCdpRelay`.) `node_modules`
  is gitignored; `package.json` keeps `ws` as the interop devDep.
