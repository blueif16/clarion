// relay-client.js — PURE framing functions for Relay protocol v1 (FROZEN).
//
// This module is the single source of truth for how the service worker frames
// outbound messages and parses inbound ones on the WebSocket wire defined in
// docs/extension-build.md. It is deliberately free of any `chrome.*` or
// `WebSocket` reference so it can be unit-tested under plain node.
//
// Wire protocol v1 (Python is the server; the extension is the client):
//   - Python -> ext (command):
//       {"id": <int>, "type": "cdp", "method": "<Domain.cmd>", "params": {…}}
//   - ext -> Python (reply):
//       {"id": <int>, "type": "cdp.result", "result": {…}}  or
//       {"id": <int>, "type": "cdp.error",  "error": "<msg>"}
//   - ext -> Python (lifecycle):
//       {"type": "session.start", "tabId": <int>, "url": "…", "title": "…"}  /
//       {"type": "session.end",   "reason": "…"}
//   - ext -> Python (CDP event, optional):
//       {"type": "cdp.event", "method": "…", "params": {…}}

/**
 * Frame a successful CDP reply.
 * @param {number} id   the request id this reply correlates to
 * @param {object} result  the raw CDP command result (`{}` if undefined)
 * @returns {string} JSON text frame
 */
export function encodeCdpResult(id, result) {
  return JSON.stringify({
    id,
    type: "cdp.result",
    result: result == null ? {} : result,
  });
}

/**
 * Frame a failed CDP reply.
 * @param {number} id   the request id this reply correlates to
 * @param {unknown} err  an Error or any value; coerced to a string message
 * @returns {string} JSON text frame
 */
export function encodeCdpError(id, err) {
  return JSON.stringify({
    id,
    type: "cdp.error",
    error: errorToMessage(err),
  });
}

/**
 * Frame the `session.start` lifecycle message sent right after attach.
 * tabId/url/title are TOP-LEVEL keys (the Python server reads them flat).
 * @param {{tabId:number, url?:string, title?:string}} info
 * @returns {string} JSON text frame
 */
export function encodeSessionStart(info) {
  const i = info || {};
  return JSON.stringify({
    type: "session.start",
    tabId: i.tabId,
    url: i.url == null ? "" : i.url,
    title: i.title == null ? "" : i.title,
  });
}

/**
 * Frame the `session.end` lifecycle message sent on detach/close.
 * @param {string} reason  why the session ended (e.g. "detached", "tab-closed")
 * @returns {string} JSON text frame
 */
export function encodeSessionEnd(reason) {
  return JSON.stringify({
    type: "session.end",
    reason: reason == null ? "" : String(reason),
  });
}

/**
 * Frame an optional forwarded CDP event (`chrome.debugger.onEvent`).
 * @param {string} method  the CDP event method, e.g. "Page.frameNavigated"
 * @param {object} params  the CDP event params
 * @returns {string} JSON text frame
 */
export function encodeCdpEvent(method, params) {
  return JSON.stringify({
    type: "cdp.event",
    method,
    params: params == null ? {} : params,
  });
}

/**
 * Parse one inbound server text frame.
 *
 * Returns a normalized object the service worker can switch on. For a `cdp`
 * command this is `{type:"cdp", id, method, params}`. Malformed JSON or a frame
 * with no `type` yields `{type: null}` (the SW ignores it). The shape mirrors
 * the protocol so the SW never has to touch raw JSON.
 *
 * @param {string} text  the raw WebSocket text frame
 * @returns {{type: string|null, id?: number, method?: string, params?: object}}
 */
export function decodeServerMessage(text) {
  let msg;
  try {
    msg = JSON.parse(text);
  } catch {
    return { type: null };
  }
  if (msg == null || typeof msg !== "object") {
    return { type: null };
  }
  const type = typeof msg.type === "string" ? msg.type : null;
  if (type === "cdp") {
    return {
      type: "cdp",
      id: msg.id,
      method: msg.method,
      params: msg.params == null ? {} : msg.params,
    };
  }
  // The server→ext direction in v1 only carries `cdp` requests; any other frame
  // is surfaced by `type` alone so the SW can ignore it. A typeless/malformed
  // frame collapses to {type:null}.
  return { type };
}

/**
 * Coerce any thrown value into a flat error message string.
 * @param {unknown} err
 * @returns {string}
 */
export function errorToMessage(err) {
  if (err == null) return "unknown error";
  if (typeof err === "string") return err;
  if (err instanceof Error) return err.message || String(err);
  if (typeof err === "object" && typeof err.message === "string") {
    return err.message;
  }
  return String(err);
}
