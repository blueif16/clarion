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

// DEBUG-ONLY visual feedback (toolbar badge + on-page HUD) + a file log sink so
// the logs are readable directly (tail /tmp/clarion-ext.log), not copy-pasted.
// Remove this import and the setBadge/pushHud/sinkLog call sites to strip it all.
import { setBadge, pushHud, pushActivity, setHudStatus, sinkLog } from "./hud.js";

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

// Debug: show an idle badge the moment the worker boots, so a blank toolbar icon
// means "worker not running" and "·" means "loaded, waiting for the shortcut".
setBadge("·", "info");
sinkLog({ phase: "service worker started — waiting for the shortcut", level: "info" });

// ---------------------------------------------------------------------------
// Shortcut → attach + connect.
// ---------------------------------------------------------------------------

chrome.commands.onCommand.addListener((command) => {
  if (command !== "start-clarion") return;
  // Instant proof the keystroke reached the worker. If the badge never changes
  // on press, the shortcut isn't registered (chrome://extensions/shortcuts).
  setBadge("…", "info");
  startClarion().catch((err) => {
    console.error("[clarion] start failed:", errorToMessage(err));
    setBadge("ERR", "err");
  });
});

async function startClarion() {
  const tab = await getActiveTab();
  if (!tab || tab.id == null) {
    console.warn("[clarion] no active tab to attach to");
    setBadge("ERR", "err");
    return;
  }
  await attachToTab(tab);
}

/**
 * Attach Clarion to a specific tab: reuse a live session on the same tab, else
 * tear down any prior session, attach the debugger, enable the CDP domains, open
 * the relay, and start browser voice. Shared by the shortcut (the active tab) and
 * the HUD "restart fresh" control (the bound tab, after a reload).
 * @param {chrome.tabs.Tab} tab
 */
async function attachToTab(tab) {
  // If a session is already live on this exact tab, do nothing (idempotent).
  if (session && session.tabId === tab.id && session.attached) {
    console.log("[clarion] already attached to tab", tab.id);
    setBadge("ON", "ok");
    pushHud(tab.id, { phase: "already attached", level: "ok" });
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

  setHudStatus(tabId, "linking");
  pushHud(tabId, { phase: "attaching debugger…", detail: session.url });
  try {
    await chrome.debugger.attach({ tabId }, CDP_VERSION);
  } catch (err) {
    // The most common cause: DevTools is open on this tab (only one debugger
    // client per tab). The error otherwise dies in the worker console.
    const msg = errorToMessage(err);
    console.error("[clarion] debugger attach failed:", msg);
    setBadge("ERR", "err");
    setHudStatus(tabId, "error");
    pushHud(tabId, {
      phase: "attach FAILED",
      detail: /already attached|DevTools/i.test(msg)
        ? "close DevTools on this tab, then retry"
        : msg,
      level: "err",
    });
    session = null;
    return;
  }
  session.attached = true;
  pushHud(tabId, { phase: "debugger attached", level: "ok" });

  // Enable the domains the §4 perception pipeline reads from.
  for (const domain of CDP_DOMAINS) {
    await chrome.debugger.sendCommand({ tabId }, `${domain}.enable`);
  }
  pushHud(tabId, { phase: "CDP domains enabled", detail: "connecting relay…" });

  ensureKeepalive();
  connectRelay();
  // Voice rides a SEPARATE LiveKit/WebRTC connection in the offscreen document
  // (additive — the CDP relay above is untouched). Start it with the session;
  // failures here never block the relay.
  startVoice().catch((err) =>
    // Surface voice-start failures in the durable log (not just the SW console),
    // so the voice path is debuggable from /tmp/clarion-ext.log like everything else.
    pushHud(tabId, { phase: "voice start FAILED", detail: errorToMessage(err), level: "err" })
  );
  console.log("[clarion] attached to tab", tabId, "—", session.url);
}

// ---------------------------------------------------------------------------
// HUD control channel: the on-page panel's "restart fresh" button posts here.
// ---------------------------------------------------------------------------

const CONTROL_TARGET = "service-worker-control";
const CONTROL_REFRESH = "clarion.refresh";

chrome.runtime.onMessage.addListener((msg) => {
  if (!msg || msg.target !== CONTROL_TARGET) return;
  if (msg.type === CONTROL_REFRESH) {
    refreshClarion().catch((err) => {
      console.error("[clarion] refresh failed:", errorToMessage(err));
      setBadge("ERR", "err");
    });
  }
});

/**
 * "Restart fresh on the site": end the current Clarion session, reload the bound
 * tab so the page returns to its initial state, then re-attach a fresh session
 * (a new relay session.start + browser voice) on the same tab. Falls back to the
 * active tab when nothing is attached. Triggered by the HUD refresh button.
 */
async function refreshClarion() {
  let tabId = session ? session.tabId : null;
  if (tabId == null) {
    const tab = await getActiveTab();
    tabId = tab ? tab.id : null;
  }
  if (tabId == null) {
    setBadge("ERR", "err");
    return;
  }
  setBadge("…", "info");

  // 1) End the current session cleanly (detaches the debugger, ends relay + voice).
  if (session) await teardown("refresh");

  // 2) Recreate the LiveKit room so a FRESH agent re-dispatches. Automatic dispatch
  //    fires only on room CREATION; rejoining the lingering room (empty_timeout
  //    300s) gets NO agent → silent voice. The local sink endpoint deletes it for
  //    us (the browser can't — it has no LiveKit API creds), then the rejoin below
  //    recreates it. No-op for the relay-only path (voice config absent).
  await resetRoom();

  // 3) Reload the site so the page starts from a clean state.
  try {
    await chrome.tabs.reload(tabId);
  } catch (err) {
    console.warn("[clarion] refresh: reload failed:", errorToMessage(err));
  }

  // 4) Re-attach Clarion on the same tab, fresh (relay + voice rejoin the new room).
  const tab = await chrome.tabs.get(tabId).catch(() => null);
  if (tab && tab.id != null) await attachToTab(tab);
}

/** Ask the local sink endpoint to delete the LiveKit room so the rejoin recreates
 * it (and the worker auto-dispatches a fresh agent). Best-effort; text/plain body =
 * no CORS preflight, same as the log POST. */
async function resetRoom() {
  let room = "";
  try {
    const cfg = await loadVoiceConfig();
    room = (cfg && cfg.ROOM_NAME) || "";
  } catch {
    /* config unavailable — the sink falls back to its default room */
  }
  try {
    await fetch("http://127.0.0.1:8772/reset-room", {
      method: "POST",
      body: room,
      keepalive: true,
    });
    sinkLog({ phase: "refresh: room reset requested", detail: room || "(default)", level: "info" });
  } catch (err) {
    sinkLog({
      phase: "refresh: room reset FAILED — agent may not rejoin",
      detail: errorToMessage(err),
      level: "warn",
    });
  }
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
    // Bail if this socket was superseded by a reconnect (s.ws reassigned to a new
    // CONNECTING/null socket) or the session ended — sending on s.ws then throws
    // "Cannot read properties of null" / "Still in CONNECTING state". Use the local
    // `ws`, which is the socket whose open just fired (guaranteed OPEN).
    if (session !== s || s.ws !== ws) return;
    s.reconnectAttempts = 0;
    // Announce the live tab — tabId/url/title are TOP-LEVEL per the frozen wire.
    ws.send(
      encodeSessionStart({ tabId: s.tabId, url: s.url, title: s.title })
    );
    console.log("[clarion] relay connected:", RELAY_URL);
    setBadge("ON", "ok");
    pushHud(s.tabId, { phase: "relay connected ✓", detail: "perceiving", level: "ok" });
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
    setBadge("RC", "warn");
    pushHud(s.tabId, {
      phase: "relay dropped — reconnecting",
      detail: "is the Python relay up on :8771?",
      level: "warn",
    });
    scheduleReconnect();
  });

  ws.addEventListener("error", () => {
    // `close` follows `error`; reconnection is driven from there.
    console.warn("[clarion] relay socket error");
    setBadge("RC", "warn");
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

  // Tear down browser-side voice (leave the room + close the offscreen doc).
  await stopVoice().catch((err) =>
    console.warn("[clarion] voice stop failed:", errorToMessage(err))
  );

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

  setHudStatus(s.tabId, "ended");
  pushHud(s.tabId, { phase: "session ended", detail: reason, level: "warn" });
  setBadge("·", "info");
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
// #5 voice: browser-side voice via an offscreen LiveKit client.
//
// Voice rides a SEPARATE LiveKit/WebRTC connection from the CDP relay above. It
// lives in an OFFSCREEN document because MV3 service workers die at ~30s idle and
// would drop the long-lived WebRTC connection; offscreen documents are exempt.
// The offscreen doc joins the SAME room as the unchanged Python `voice_entry`
// worker, publishes the user's mic, and plays the agent's incoming audio.
//
// getUserMedia() cannot prompt from an offscreen doc/side panel/popup, so the
// mic grant is first obtained from a FULL extension tab (request-mic.html) and
// then remembered by Chrome for the extension origin.
// ---------------------------------------------------------------------------

const OFFSCREEN_URL = "offscreen.html";
const REQUEST_MIC_URL = "request-mic.html";

// Message channel between the worker and the offscreen / request-mic pages.
// Kept in sync with offscreen.js and request-mic.js.
const VOICE_MSG = {
  CONNECT: "voice.connect", // SW → offscreen
  DISCONNECT: "voice.disconnect", // SW → offscreen
  STATE: "voice.state", // offscreen → SW
  LOG: "voice.log", // offscreen → SW (diagnostic line → HUD + /tmp/clarion-ext.log)
  MIC_RESULT: "voice.mic-result", // request-mic → SW
};
const OFFSCREEN_TARGET = "offscreen-voice";
const SW_VOICE_TARGET = "service-worker-voice";

// Browser-side LiveKit connection state → HUD orb status. The agent's own state
// machine (listening/thinking/speaking) then takes over via the worker's
// `[agent]` log lines once the room is live; this covers the connect/teardown
// edges that machine doesn't.
const VOICE_ORB_STATUS = {
  connecting: "linking",
  connected: "listening",
  error: "error",
  disconnected: "ended",
};

/** Resolver for an in-flight mic-permission request, or null. */
let micGrantResolver = null;

/**
 * Load the voice config from the gitignored config.js (falls back to nothing if
 * absent — voice is then skipped and the CDP relay still works). config.js does
 * `export default { LIVEKIT_URL, ROOM_NAME, TOKEN }`.
 * @returns {Promise<{LIVEKIT_URL:string, ROOM_NAME?:string, TOKEN:string}|null>}
 */
async function loadVoiceConfig() {
  // Primary: dynamic import of the ESM config. Some MV3 service-worker runtimes
  // restrict dynamic import(), so fall back to fetching + parsing the text (the
  // file's shape is fixed: export default { LIVEKIT_URL, ROOM_NAME, TOKEN }).
  const url = chrome.runtime.getURL("config.js");
  try {
    const mod = await import(url);
    if (mod && mod.default && mod.default.LIVEKIT_URL && mod.default.TOKEN) {
      return mod.default;
    }
  } catch (err) {
    sinkLog({
      phase: "voice: dynamic import(config.js) failed — trying fetch",
      detail: errorToMessage(err),
      level: "warn",
    });
  }
  try {
    const text = await (await fetch(url)).text();
    const pick = (k) =>
      (text.match(new RegExp(k + '\\s*:\\s*"([^"]*)"')) || [])[1] || "";
    const cfg = {
      LIVEKIT_URL: pick("LIVEKIT_URL"),
      ROOM_NAME: pick("ROOM_NAME"),
      TOKEN: pick("TOKEN"),
    };
    return cfg.LIVEKIT_URL && cfg.TOKEN ? cfg : null;
  } catch (err) {
    sinkLog({ phase: "voice: fetch(config.js) failed", detail: errorToMessage(err), level: "err" });
    return null;
  }
}

/**
 * Whether the mic permission is already granted for the extension origin. Uses
 * the Permissions API where available; a `prompt`/`denied` state means we must
 * open the full-tab request page.
 * @returns {Promise<boolean>}
 */
async function micAlreadyGranted() {
  try {
    if (!navigator.permissions || !navigator.permissions.query) return false;
    const status = await navigator.permissions.query({ name: "microphone" });
    return status.state === "granted";
  } catch {
    return false;
  }
}

/**
 * Ensure the mic grant: if not already granted, open request-mic.html (a full
 * extension tab — the only context that can prompt) and await its result.
 * @returns {Promise<boolean>} true if granted
 */
async function ensureMicPermission() {
  if (await micAlreadyGranted()) return true;
  const granted = await new Promise((resolve) => {
    micGrantResolver = resolve;
    chrome.tabs.create({ url: chrome.runtime.getURL(REQUEST_MIC_URL) });
    // Safety timeout so a closed/ignored tab doesn't hang the session forever.
    setTimeout(() => {
      if (micGrantResolver === resolve) {
        micGrantResolver = null;
        resolve(false);
      }
    }, 120000);
  });
  return granted;
}

/** True if an offscreen document already exists (the one-doc-at-a-time rule). */
async function hasOffscreenDocument() {
  if (chrome.offscreen && chrome.offscreen.hasDocument) {
    return chrome.offscreen.hasDocument();
  }
  // Fallback for older runtimes: inspect existing contexts.
  if (chrome.runtime.getContexts) {
    const contexts = await chrome.runtime.getContexts({
      contextTypes: ["OFFSCREEN_DOCUMENT"],
      documentUrls: [chrome.runtime.getURL(OFFSCREEN_URL)],
    });
    return contexts.length > 0;
  }
  return false;
}

/** Create the offscreen document if one is not already open. */
async function ensureOffscreenDocument() {
  if (await hasOffscreenDocument()) return;
  await chrome.offscreen.createDocument({
    url: OFFSCREEN_URL,
    reasons: ["USER_MEDIA", "WEB_RTC", "AUDIO_PLAYBACK"],
    justification: "voice co-pilot mic + audio",
  });
}

/**
 * Start browser-side voice: ensure the mic grant, open the offscreen document,
 * and post it the LiveKit room config. No-op (with a log) if config.js is absent
 * or the mic is denied — the CDP relay is unaffected either way.
 */
async function startVoice() {
  // Every gate is sink-logged so a silent voice failure is visible in
  // /tmp/clarion-ext.log (the offscreen + mic path is otherwise a black box).
  sinkLog({ phase: "voice: starting", level: "info" });
  const cfg = await loadVoiceConfig();
  if (!cfg || !cfg.LIVEKIT_URL || !cfg.TOKEN) {
    sinkLog({
      phase: "voice: NO config.js — skipping",
      detail: "config.js failed to import or is missing LIVEKIT_URL/TOKEN",
      level: "err",
    });
    return;
  }
  sinkLog({ phase: "voice: config loaded", detail: cfg.ROOM_NAME || "" });

  const granted = await ensureMicPermission();
  if (!granted) {
    sinkLog({
      phase: "voice: mic NOT granted — skipping",
      detail: "grant the mic in the request-mic tab, then re-run the shortcut",
      level: "err",
    });
    return;
  }
  sinkLog({ phase: "voice: mic granted", level: "ok" });

  await ensureOffscreenDocument();
  sinkLog({ phase: "voice: offscreen ready — sending CONNECT" });
  chrome.runtime.sendMessage({
    type: VOICE_MSG.CONNECT,
    target: OFFSCREEN_TARGET,
    livekitUrl: cfg.LIVEKIT_URL,
    token: cfg.TOKEN,
    roomName: cfg.ROOM_NAME || "",
    // Optional mic override (else the offscreen doc auto-prefers a real device).
    micDeviceId: cfg.MIC_DEVICE_ID || "",
    micMatch: cfg.MIC_MATCH || "",
  });
}

/**
 * Stop browser-side voice: tell the offscreen doc to leave the room, then close
 * the document. Idempotent and safe when voice was never started.
 */
async function stopVoice() {
  if (!(await hasOffscreenDocument())) return;
  try {
    chrome.runtime.sendMessage({
      type: VOICE_MSG.DISCONNECT,
      target: OFFSCREEN_TARGET,
    });
  } catch {
    /* offscreen may already be gone */
  }
  try {
    await chrome.offscreen.closeDocument();
  } catch {
    /* already closed */
  }
  console.log("[clarion] voice: offscreen closed");
}

// Voice messages back from the offscreen / request-mic pages.
chrome.runtime.onMessage.addListener((msg) => {
  if (!msg || msg.target !== SW_VOICE_TARGET) return;
  if (msg.type === VOICE_MSG.MIC_RESULT) {
    if (micGrantResolver) {
      const resolve = micGrantResolver;
      micGrantResolver = null;
      resolve(!!msg.granted);
    }
  } else if (msg.type === VOICE_MSG.STATE) {
    console.log("[clarion] voice state:", msg.state, msg.detail || "");
    const level =
      msg.state === "error" ? "err" : msg.state === "connected" ? "ok" : "info";
    sinkLog({ phase: `voice ${msg.state}`, detail: msg.detail || "", level });
    setBadge(
      msg.state === "connected" ? "MIC" : msg.state === "error" ? "ERR" : "·",
      level
    );
    // Reflect the voice connection on the on-page orb (best-effort; needs a tab).
    const orb = VOICE_ORB_STATUS[msg.state];
    if (orb && session && session.tabId != null) setHudStatus(session.tabId, orb);
  } else if (msg.type === VOICE_MSG.LOG) {
    // A decided ACTION (Feature A) carries an `activity` payload — render it on the
    // on-page action-trace feed (toast + panel Activity history) instead of the
    // diagnostic event log. Needs an attached tab; no file-sink mirror.
    if (msg.activity) {
      // Mark the file sink so a fired toast is PROVABLE from /tmp/clarion-ext.log
      // (activity frames otherwise never touch the sink → undebuggable browser-side).
      const a = msg.activity;
      sinkLog({
        phase: "[activity] pushActivity",
        detail: `${a.kind} ${a.target} status=${a.status} tab=${session && session.tabId}`,
        level: "info",
      });
      if (session && session.tabId != null) pushActivity(session.tabId, msg.activity);
      return;
    }
    // Diagnostic line from the offscreen voice doc (mic device + signal) OR a
    // worker line forwarded off the clarion-log topic. Route to the on-page HUD if
    // a tab is attached, else the file sink. Worker lines (msg.fromWorker) are
    // ALREADY in ext.log via the worker's own POST, so we skip the sink for them
    // to avoid double-logging — they still render on the HUD.
    const entry = { phase: msg.phase || "voice", detail: msg.detail || "", level: msg.level || "info" };
    const sink = !msg.fromWorker;
    if (session && session.tabId != null) pushHud(session.tabId, entry, { sink });
    else if (sink) sinkLog(entry);
  }
});
