// service-worker.js — Clarion MV3 background service worker (ES module).
//
// Role: a DUMB CDP relay. A keyboard shortcut attaches `chrome.debugger` to the
// active tab, opens a WebSocket to the local Python relay, and bridges the wire:
// inbound `cdp` requests are forwarded verbatim to `chrome.debugger.sendCommand`
// and the result is piped back by `id`. The worker NEVER interprets CDP — it only
// forwards `method`+`params` and correlates replies.
//
// The framing lives in the pure, chrome-free `relay-client.js` module so it can be
// unit-tested under node; this file owns only the chrome.* + WebSocket plumbing.
//
// Relay protocol v1 is FROZEN — see docs/extension-build.md.

import {
  encodeCdpResult,
  encodeCdpError,
  encodeSessionStart,
  encodeSessionEnd,
  encodeCdpEvent,
  decodeServerMessage,
  errorToMessage,
} from "./relay-client.js";

// --- configuration ----------------------------------------------------------

const RELAY_URL = "ws://127.0.0.1:8771";
const CDP_VERSION = "1.3";
// CDP domains ExtensionActuator expects enabled (mirrors PlaywrightActuator).
const CDP_DOMAINS = ["DOM", "Accessibility", "DOMSnapshot", "Runtime", "Page"];
const KEEPALIVE_ALARM = "clarion-keepalive";
const KEEPALIVE_PERIOD_MIN = 0.33; // ~20s — the chrome.alarms minimum-friendly backstop.
const RECONNECT_BASE_MS = 500;
const RECONNECT_MAX_MS = 10000;

// --- single-session state ---------------------------------------------------
//
// The relay is 1:1 with a tab. We keep one session object; re-running the
// shortcut on a new tab tears the old one down first.

/**
 * @typedef {Object} Session
 * @property {number} tabId
 * @property {WebSocket|null} ws
 * @property {boolean} attached
 * @property {boolean} closing       // tearing down on purpose (no reconnect)
 * @property {number} reconnectAttempts
 * @property {string} url
 * @property {string} title
 */

/** @type {Session|null} */
let session = null;

// ---------------------------------------------------------------------------
// Shortcut → attach + connect.
// ---------------------------------------------------------------------------

chrome.commands.onCommand.addListener((command) => {
  if (command !== "start-clarion") return;
  startClarion().catch((err) => {
    console.error("[clarion] start failed:", errorToMessage(err));
  });
});

async function startClarion() {
  const tab = await getActiveTab();
  if (!tab || tab.id == null) {
    console.warn("[clarion] no active tab to attach to");
    return;
  }

  // If a session is already live on this exact tab, do nothing (idempotent).
  if (session && session.tabId === tab.id && session.attached) {
    console.log("[clarion] already attached to tab", tab.id);
    return;
  }
  // Otherwise tear down any prior session before starting fresh.
  if (session) {
    await teardown("restart");
  }

  const tabId = tab.id;
  session = {
    tabId,
    ws: null,
    attached: false,
    closing: false,
    reconnectAttempts: 0,
    url: tab.url || "",
    title: tab.title || "",
  };

  await chrome.debugger.attach({ tabId }, CDP_VERSION);
  session.attached = true;

  // Enable the domains the §4 perception pipeline reads from.
  for (const domain of CDP_DOMAINS) {
    await chrome.debugger.sendCommand({ tabId }, `${domain}.enable`);
  }

  ensureKeepalive();
  connectRelay();
  console.log("[clarion] attached to tab", tabId, "—", session.url);
}

async function getActiveTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab || null;
}

// ---------------------------------------------------------------------------
// WebSocket relay: connect, frame, forward.
// ---------------------------------------------------------------------------

function connectRelay() {
  if (!session) return;
  const s = session;

  let ws;
  try {
    ws = new WebSocket(RELAY_URL);
  } catch (err) {
    console.error("[clarion] WebSocket construct failed:", errorToMessage(err));
    scheduleReconnect();
    return;
  }
  s.ws = ws;

  ws.addEventListener("open", () => {
    if (session !== s) return;
    s.reconnectAttempts = 0;
    // Announce the live tab — tabId/url/title are TOP-LEVEL per the frozen wire.
    s.ws.send(
      encodeSessionStart({ tabId: s.tabId, url: s.url, title: s.title })
    );
    console.log("[clarion] relay connected:", RELAY_URL);
  });

  ws.addEventListener("message", (event) => {
    if (session !== s) return;
    handleServerMessage(s, event.data);
  });

  ws.addEventListener("close", () => {
    if (session !== s) return;
    s.ws = null;
    if (s.closing) return;
    console.warn("[clarion] relay closed — reconnecting");
    scheduleReconnect();
  });

  ws.addEventListener("error", () => {
    // `close` follows `error`; reconnection is driven from there.
    console.warn("[clarion] relay socket error");
  });
}

function scheduleReconnect() {
  if (!session || session.closing) return;
  const s = session;
  const delay = Math.min(
    RECONNECT_BASE_MS * 2 ** s.reconnectAttempts,
    RECONNECT_MAX_MS
  );
  s.reconnectAttempts += 1;
  setTimeout(() => {
    if (session === s && !s.closing && s.attached) {
      connectRelay();
    }
  }, delay);
}

/**
 * Route one inbound server frame. The only request type we act on is `cdp`:
 * forward it verbatim to chrome.debugger and reply by `id`.
 * @param {Session} s
 * @param {string} raw
 */
function handleServerMessage(s, raw) {
  const text = typeof raw === "string" ? raw : String(raw);
  const msg = decodeServerMessage(text);
  if (msg.type === "cdp") {
    forwardCdp(s, msg.id, msg.method, msg.params);
  }
  // Other inbound types are not part of the server→ext direction in v1; ignore.
}

/**
 * Forward a single CDP command to the debugger target and reply by id.
 * @param {Session} s
 * @param {number} id
 * @param {string} method
 * @param {object} params
 */
async function forwardCdp(s, id, method, params) {
  try {
    const result = await chrome.debugger.sendCommand(
      { tabId: s.tabId },
      method,
      params || {}
    );
    if (session === s && s.ws && s.ws.readyState === WebSocket.OPEN) {
      s.ws.send(encodeCdpResult(id, result));
    }
  } catch (err) {
    if (session === s && s.ws && s.ws.readyState === WebSocket.OPEN) {
      s.ws.send(encodeCdpError(id, err));
    }
  }
}

// ---------------------------------------------------------------------------
// Forward CDP events to the relay (optional; the server tolerates these).
// ---------------------------------------------------------------------------

chrome.debugger.onEvent.addListener((source, method, params) => {
  if (!session || source.tabId !== session.tabId) return;
  const s = session;
  if (s.ws && s.ws.readyState === WebSocket.OPEN) {
    s.ws.send(encodeCdpEvent(method, params));
  }
});

// ---------------------------------------------------------------------------
// Teardown paths: debugger detach, tab close.
// ---------------------------------------------------------------------------

chrome.debugger.onDetach.addListener((source, reason) => {
  if (!session || source.tabId !== session.tabId) return;
  // The debugger is already gone; mark unattached so teardown won't re-detach.
  session.attached = false;
  teardown(`detach:${reason}`).catch((err) =>
    console.error("[clarion] teardown error:", errorToMessage(err))
  );
});

chrome.tabs.onRemoved.addListener((tabId) => {
  if (!session || session.tabId !== tabId) return;
  session.attached = false; // tab is gone; debugger went with it.
  teardown("tab-closed").catch((err) =>
    console.error("[clarion] teardown error:", errorToMessage(err))
  );
});

/**
 * Tear the session down cleanly: tell the relay, close the socket, detach the
 * debugger, and drop the keepalive. Safe to call from any path; idempotent.
 * @param {string} reason
 */
async function teardown(reason) {
  const s = session;
  if (!s) return;
  s.closing = true;

  if (s.ws) {
    try {
      if (s.ws.readyState === WebSocket.OPEN) {
        s.ws.send(encodeSessionEnd(reason));
      }
      s.ws.close();
    } catch {
      /* socket may already be gone */
    }
    s.ws = null;
  }

  if (s.attached) {
    try {
      await chrome.debugger.detach({ tabId: s.tabId });
    } catch {
      /* already detached */
    }
    s.attached = false;
  }

  session = null;
  clearKeepalive();
  console.log("[clarion] session ended:", reason);
}

// ---------------------------------------------------------------------------
// SW-lifecycle keepalive backstop.
//
// The live chrome.debugger session plus active WS traffic keep the worker alive
// in practice, but a ~20s chrome.alarms tick is a cheap backstop against the SW
// being evicted mid-session (which would silently drop the relay). The handler
// does trivial work (reads readyState) purely to wake the worker.
// ---------------------------------------------------------------------------

function ensureKeepalive() {
  chrome.alarms.create(KEEPALIVE_ALARM, {
    periodInMinutes: KEEPALIVE_PERIOD_MIN,
  });
}

function clearKeepalive() {
  chrome.alarms.clear(KEEPALIVE_ALARM);
}

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name !== KEEPALIVE_ALARM) return;
  if (!session) {
    clearKeepalive();
    return;
  }
  // Touch the socket to keep the worker warm; reconnect if it has dropped.
  if (!session.ws && session.attached && !session.closing) {
    connectRelay();
  }
});

// ---------------------------------------------------------------------------
// #5 voice: create offscreen document here
//
// The next feature wires browser-side voice: create an offscreen document that
// runs an @livekit/client and joins the same room as the unchanged voice_entry
// worker. Intentionally NOT built here — this is only the marked extension point.
//   e.g. await chrome.offscreen.createDocument({
//          url: "offscreen.html",
//          reasons: ["USER_MEDIA"],
//          justification: "Clarion browser-side voice co-pilot",
//        });
// ---------------------------------------------------------------------------
