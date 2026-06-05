// relay-framing.test.mjs — round-trip every relay-client.js framing function
// against the FROZEN Relay protocol v1 shapes (docs/extension-build.md).
// No chrome, no net.

import { test } from "node:test";
import assert from "node:assert/strict";

import {
  encodeCdpResult,
  encodeCdpError,
  encodeSessionStart,
  encodeSessionEnd,
  encodeCdpEvent,
  decodeServerMessage,
  errorToMessage,
} from "../relay-client.js";

test("encodeCdpResult frames {id,type:'cdp.result',result}", () => {
  const frame = JSON.parse(encodeCdpResult(7, { root: { nodeId: 1 } }));
  assert.deepEqual(frame, {
    id: 7,
    type: "cdp.result",
    result: { root: { nodeId: 1 } },
  });
});

test("encodeCdpResult defaults a missing result to {}", () => {
  const frame = JSON.parse(encodeCdpResult(3, undefined));
  assert.deepEqual(frame, { id: 3, type: "cdp.result", result: {} });
});

test("encodeCdpError frames {id,type:'cdp.error',error} from an Error", () => {
  const frame = JSON.parse(encodeCdpError(9, new Error("boom")));
  assert.deepEqual(frame, { id: 9, type: "cdp.error", error: "boom" });
});

test("encodeCdpError coerces a string error verbatim", () => {
  const frame = JSON.parse(encodeCdpError(2, "no such node"));
  assert.equal(frame.type, "cdp.error");
  assert.equal(frame.error, "no such node");
});

test("encodeSessionStart frames flat tabId/url/title", () => {
  const frame = JSON.parse(
    encodeSessionStart({ tabId: 42, url: "https://x.test/", title: "X" })
  );
  assert.deepEqual(frame, {
    type: "session.start",
    tabId: 42,
    url: "https://x.test/",
    title: "X",
  });
});

test("encodeSessionStart defaults missing url/title to empty strings", () => {
  const frame = JSON.parse(encodeSessionStart({ tabId: 1 }));
  assert.deepEqual(frame, {
    type: "session.start",
    tabId: 1,
    url: "",
    title: "",
  });
});

test("encodeSessionEnd frames {type:'session.end',reason}", () => {
  const frame = JSON.parse(encodeSessionEnd("tab-closed"));
  assert.deepEqual(frame, { type: "session.end", reason: "tab-closed" });
});

test("encodeCdpEvent frames {type:'cdp.event',method,params}", () => {
  const frame = JSON.parse(
    encodeCdpEvent("Page.frameNavigated", { frame: { id: "F" } })
  );
  assert.deepEqual(frame, {
    type: "cdp.event",
    method: "Page.frameNavigated",
    params: { frame: { id: "F" } },
  });
});

test("decodeServerMessage parses a cdp command into {type,id,method,params}", () => {
  const incoming = JSON.stringify({
    id: 11,
    type: "cdp",
    method: "DOM.getDocument",
    params: { depth: -1, pierce: true },
  });
  const msg = decodeServerMessage(incoming);
  assert.equal(msg.type, "cdp");
  assert.equal(msg.id, 11);
  assert.equal(msg.method, "DOM.getDocument");
  assert.deepEqual(msg.params, { depth: -1, pierce: true });
});

test("decodeServerMessage defaults missing cdp params to {}", () => {
  const incoming = JSON.stringify({ id: 5, type: "cdp", method: "Page.enable" });
  const msg = decodeServerMessage(incoming);
  assert.deepEqual(msg.params, {});
});

test("decodeServerMessage returns {type:null} on malformed JSON", () => {
  assert.deepEqual(decodeServerMessage("{not json"), { type: null });
});

test("decodeServerMessage returns {type:null} for a typeless frame", () => {
  assert.deepEqual(decodeServerMessage(JSON.stringify({ id: 1 })), {
    type: null,
  });
});

test("full encode→decode round-trip preserves a cdp request id+method", () => {
  // Simulate the server framing the SAME shape the Python relay sends, then the
  // SW decoding it and replying — the two halves must agree on id correlation.
  const serverFrame = JSON.stringify({
    id: 99,
    type: "cdp",
    method: "Accessibility.getFullAXTree",
    params: {},
  });
  const decoded = decodeServerMessage(serverFrame);
  const reply = JSON.parse(encodeCdpResult(decoded.id, { nodes: [] }));
  assert.equal(reply.id, 99);
  assert.equal(reply.type, "cdp.result");
  assert.deepEqual(reply.result, { nodes: [] });
});

test("errorToMessage handles string, Error, object, and null", () => {
  assert.equal(errorToMessage("x"), "x");
  assert.equal(errorToMessage(new Error("y")), "y");
  assert.equal(errorToMessage({ message: "z" }), "z");
  assert.equal(errorToMessage(null), "unknown error");
});
