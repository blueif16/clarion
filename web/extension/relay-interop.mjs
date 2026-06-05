// relay-interop.mjs — prove the extension's framing interoperates with a RUNNING
// Python `WebSocketCdpRelay` (ws://127.0.0.1:8771). This plays the EXTENSION side:
// it connects as the client, sends `session.start`, then answers each inbound
// `cdp` request with a canned result — exactly as service-worker.js would.
//
// It exercises the same pure framing module the service worker uses, so a green
// run is evidence the SW wire matches the real server.
//
// Requires the `ws` npm package (devDependency). If it isn't installed this
// script PRINTS a skip notice and exits 0 — it never blocks the framing tests.
//
// Usage:
//   1. Start the Python server (in the agent venv), e.g. a tiny driver that does
//        relay = await WebSocketCdpRelay().start()
//        await relay.wait_connected()
//        print(await relay.send("DOM.getDocument", {"depth": -1, "pierce": True}))
//   2. node web/extension/relay-interop.mjs

import {
  encodeSessionStart,
  encodeCdpResult,
  encodeCdpError,
  decodeServerMessage,
} from "./relay-client.js";

const RELAY_URL = process.env.CLARION_RELAY_URL || "ws://127.0.0.1:8771";

// Canned CDP results keyed by method — stand-ins for chrome.debugger.sendCommand.
const CANNED = {
  "DOM.getDocument": { root: { nodeId: 1, backendNodeId: 1 } },
  "Accessibility.getFullAXTree": { nodes: [] },
  "DOMSnapshot.captureSnapshot": { documents: [], strings: [] },
  "Page.enable": {},
  "DOM.enable": {},
  "Runtime.enable": {},
};

let WebSocketImpl;
try {
  ({ WebSocket: WebSocketImpl } = await import("ws"));
} catch {
  console.log(
    "[interop] SKIP — the `ws` package is not installed.\n" +
      "          Run `npm install` in web/extension/ to enable this check."
  );
  process.exit(0);
}

const ws = new WebSocketImpl(RELAY_URL);
let exchanges = 0;

ws.on("open", () => {
  console.log("[interop] connected to", RELAY_URL);
  ws.send(
    encodeSessionStart({
      tabId: 1234,
      url: "https://interop.test/",
      title: "Clarion interop",
    })
  );
});

ws.on("message", (data) => {
  const msg = decodeServerMessage(data.toString());
  if (msg.type !== "cdp") return;
  exchanges += 1;
  const canned = CANNED[msg.method];
  if (canned !== undefined) {
    ws.send(encodeCdpResult(msg.id, canned));
    console.log(`[interop] answered #${msg.id} ${msg.method} → result`);
  } else {
    ws.send(encodeCdpError(msg.id, `no canned result for ${msg.method}`));
    console.log(`[interop] answered #${msg.id} ${msg.method} → error`);
  }
});

ws.on("close", () => {
  console.log(`[interop] closed after ${exchanges} exchange(s).`);
  process.exit(exchanges > 0 ? 0 : 1);
});

ws.on("error", (err) => {
  console.error(
    "[interop] socket error — is the Python relay running on " +
      `${RELAY_URL}?\n          ${err.message}`
  );
  process.exit(1);
});

// Safety valve: exit after 30s so a hung server never wedges CI.
setTimeout(() => {
  console.log(`[interop] timeout — completed ${exchanges} exchange(s).`);
  ws.close();
  process.exit(exchanges > 0 ? 0 : 1);
}, 30000);
