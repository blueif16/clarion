// hud.js — DEBUG-ONLY visual feedback for the Clarion extension.
//
// Two purely-diagnostic surfaces, both trivially removable:
//   1. the toolbar badge (chrome.action) — a short connection-state code so you
//      can tell at a glance whether the shortcut fired and the relay is live;
//   2. an on-page HUD overlay (injected via chrome.scripting) — a rolling log of
//      lifecycle phases so a developer can SEE which phase the session is in.
//
// Everything here is gated by DEBUG_HUD. Flip it to false (or delete the import
// + call sites in service-worker.js) to hide every debug surface — nothing in
// this file is load-bearing for the relay.

export const DEBUG_HUD = true;

// Durable log sink (scripts/clarion-logsink.py). The SW POSTs each phase line here
// as text/plain (a CORS "simple" request — no preflight) so the logs land in
// /tmp/clarion-ext.log and can be read directly, not copy-pasted out of DevTools.
const SINK_URL = "http://127.0.0.1:8772/log";

const BADGE_BG = {
  info: "#3b82f6", // blue   — in progress / idle
  ok: "#16a34a", //   green  — relay connected
  warn: "#d97706", //  amber  — dropped / reconnecting
  err: "#dc2626", //   red    — failed
};

/** Set the toolbar badge text + colour. No-op if DEBUG_HUD is off. */
export function setBadge(text, level = "info") {
  if (!DEBUG_HUD || !chrome.action) return;
  try {
    chrome.action.setBadgeBackgroundColor({ color: BADGE_BG[level] || BADGE_BG.info });
    chrome.action.setBadgeText({ text: text || "" });
  } catch {
    /* action API unavailable — ignore */
  }
}

/**
 * Forward one log line to the durable sink so it lands in /tmp/clarion-ext.log.
 * Best-effort and fire-and-forget — if the sink isn't running, this is a no-op.
 * Always also mirrors to the SW console. @param {{phase:string,detail?:string,level?:string}} entry
 */
export function sinkLog(entry) {
  const line = `${(entry.level || "info").toUpperCase()} | ${entry.phase}${
    entry.detail ? " | " + entry.detail : ""
  }`;
  // SW console (visible in the service-worker inspector) …
  console.log("[clarion]", line);
  if (!DEBUG_HUD) return;
  // … and the file sink (text/plain body = no CORS preflight).
  try {
    fetch(SINK_URL, { method: "POST", body: line, keepalive: true }).catch(() => {});
  } catch {
    /* sink down — non-fatal */
  }
}

/**
 * Push one phase line into the on-page HUD overlay on `tabId` AND the file sink.
 * The overlay is injected into the ISOLATED world so the page's CSP/scripts are
 * untouched. Best-effort: restricted pages (chrome://, web store) fall back to the
 * badge + sink. @param {number|null|undefined} tabId
 * @param {{phase:string, detail?:string, level?:string}} entry
 */
export async function pushHud(tabId, entry) {
  // The sink fires regardless of whether the on-page overlay can be injected, so
  // even attach failures on a restricted page are captured in the log file.
  sinkLog(entry);
  if (!DEBUG_HUD || tabId == null || !chrome.scripting) return;
  try {
    await chrome.scripting.executeScript({
      target: { tabId },
      world: "ISOLATED",
      func: _renderHud,
      args: [entry],
    });
  } catch {
    /* restricted page or the tab is gone — the badge still carries the state */
  }
}

/**
 * Page-side renderer, serialized and injected by pushHud. Self-contained: it
 * must NOT close over any module-scope binding (only its `entry` argument and
 * page globals like document/Date).
 */
function _renderHud(entry) {
  const ID = "__clarion_debug_hud__";
  const COLORS = { info: "#7fd1ff", ok: "#7CFC9A", warn: "#ffd479", err: "#ff7b7b" };
  let box = document.getElementById(ID);
  if (!box) {
    box = document.createElement("div");
    box.id = ID;
    // pointer-events:auto + user-select:text so the log can be selected and copied.
    box.style.cssText =
      "position:fixed;top:12px;right:12px;z-index:2147483647;" +
      "font:12px/1.45 ui-monospace,SFMono-Regular,Menlo,monospace;" +
      "background:rgba(17,17,17,.93);color:#eee;padding:10px 12px;" +
      "border-radius:8px;max-width:380px;box-shadow:0 6px 20px rgba(0,0,0,.45);" +
      "pointer-events:auto;user-select:text;-webkit-user-select:text;backdrop-filter:blur(2px)";

    const header = document.createElement("div");
    header.style.cssText =
      "display:flex;align-items:center;justify-content:space-between;" +
      "gap:10px;margin-bottom:5px";
    const title = document.createElement("div");
    title.textContent = "CLARION · debug";
    title.style.cssText = "color:#fff;font-weight:600;letter-spacing:.04em";

    const copy = document.createElement("button");
    copy.textContent = "⧉ copy";
    copy.style.cssText =
      "all:unset;cursor:pointer;color:#7fd1ff;font:11px ui-monospace,monospace;" +
      "border:1px solid #335;border-radius:5px;padding:1px 6px";
    copy.addEventListener("click", () => {
      const log = document.getElementById(ID + "_log");
      const text = log ? log.innerText : "";
      // Try the async clipboard (needs the click gesture); fall back to selecting
      // the text so the user can just press Cmd/Ctrl+C.
      const ok = () => {
        copy.textContent = "✓ copied";
        setTimeout(() => (copy.textContent = "⧉ copy"), 1200);
      };
      try {
        navigator.clipboard.writeText(text).then(ok, () => _selectNode(log));
      } catch {
        _selectNode(log);
      }
    });

    header.appendChild(title);
    header.appendChild(copy);
    const log = document.createElement("div");
    log.id = ID + "_log";
    box.appendChild(header);
    box.appendChild(log);
    (document.body || document.documentElement).appendChild(box);
  }
  const log = document.getElementById(ID + "_log");
  const line = document.createElement("div");
  const head = document.createElement("span");
  head.textContent = entry.phase;
  head.style.color = COLORS[entry.level] || "#eee";
  head.style.fontWeight = "600";
  line.appendChild(document.createTextNode(new Date().toLocaleTimeString() + "  "));
  line.appendChild(head);
  if (entry.detail) line.appendChild(document.createTextNode("  " + entry.detail));
  log.appendChild(line);
  while (log.childNodes.length > 10) log.removeChild(log.firstChild);

  function _selectNode(node) {
    if (!node) return;
    const range = document.createRange();
    range.selectNodeContents(node);
    const sel = window.getSelection();
    sel.removeAllRanges();
    sel.addRange(range);
  }
}
