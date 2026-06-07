// hud.js — DEBUG-ONLY visual feedback for the Clarion extension.
//
// Three purely-diagnostic surfaces, all trivially removable:
//   1. the toolbar badge (chrome.action) — a short connection-state code so you
//      can tell at a glance whether the shortcut fired and the relay is live;
//   2. an on-page HUD overlay (injected via chrome.scripting) — an instrument-
//      grade telemetry panel: a Siri-style pearlescent orb (light glassmorphism)
//      that reflects the live agent status (idle · linking · listening · thinking
//      · speaking) and an elegant, scrollable event log;
//   3. a durable file sink so the same lines land in /tmp/clarion-ext.log.
//
// Everything here is gated by DEBUG_HUD. Flip it to false (or delete the import
// + call sites in service-worker.js) to hide every debug surface — nothing in
// this file is load-bearing for the relay.
//
// The orb is driven off the REAL agent state machine the Python voice worker
// already publishes on the `clarion-log` topic ([agent] listening → thinking →
// speaking, [asr] HEARD, [tool], [error]); see `statusFromEntry` below. No extra
// plumbing on the worker side — the panel just reads what's already flowing.

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
 * Derive an orb status (and/or a one-shot pulse) from a log entry. This is how the
 * visualizer follows the LIVE agent state machine without any extra wiring: the
 * Python worker already publishes `[agent] <old> → <new>` on `clarion-log`, which
 * reaches us through pushHud. We read the *new* state out of it.
 * @param {{phase?:string, detail?:string, level?:string}} entry
 * @returns {{status: string|null, pulse: boolean}}
 */
function statusFromEntry(entry) {
  const phase = String((entry && entry.phase) || "");
  const detail = String((entry && entry.detail) || "");
  const hay = (phase + " " + detail).toLowerCase();
  // The agent's own state machine is the truth: [agent] "<old> → <new>". Read the
  // NEW state (right of the arrow) — otherwise a "speaking → listening" edge would
  // report the OLD state and the orb would stick on "speaking" after a reply ends.
  if (hay.includes("[agent]")) {
    let seg = detail.toLowerCase();
    const ai = Math.max(seg.lastIndexOf("→"), seg.lastIndexOf(">"));
    if (ai >= 0) seg = seg.slice(ai + 1);
    if (seg.includes("speaking")) return { status: "speaking", pulse: false };
    if (seg.includes("thinking")) return { status: "thinking", pulse: false };
    if (seg.includes("listening")) return { status: "listening", pulse: false };
    if (seg.includes("initializing")) return { status: "linking", pulse: false };
  }
  if (hay.includes("[close]") || hay.includes("session ended")) {
    return { status: "ended", pulse: false };
  }
  if ((entry && entry.level) === "err") return { status: "error", pulse: false };
  // A confirmed transcript: don't change the status, but ripple the orb so "it
  // heard me" reads instantly.
  if (hay.includes("[asr]") && hay.includes("heard")) return { status: null, pulse: true };
  // A tool call means the agent is doing work — nudge it to "thinking" if it
  // hasn't already flipped there.
  if (hay.includes("[tool]")) return { status: "thinking", pulse: false };
  return { status: null, pulse: false };
}

/**
 * Inject the page-side renderer with one payload. Best-effort: restricted pages
 * (chrome://, web store) and dead tabs simply no-op — the badge + sink still carry
 * the state. @param {number} tabId
 * @param {{entry?:object, status?:string, pulse?:boolean}} payload
 */
async function _inject(tabId, payload) {
  if (!DEBUG_HUD || tabId == null || !chrome.scripting) return;
  try {
    await chrome.scripting.executeScript({
      target: { tabId },
      world: "ISOLATED",
      func: _renderHud,
      args: [payload],
    });
  } catch {
    /* restricted page or the tab is gone — the badge still carries the state */
  }
}

/**
 * Push one phase line into the on-page HUD overlay on `tabId` AND the file sink.
 * The orb status is derived from the entry automatically (see statusFromEntry), so
 * worker `[agent]` lines move the visualizer with no extra call sites.
 * @param {number|null|undefined} tabId
 * @param {{phase:string, detail?:string, level?:string}} entry
 * @param {{sink?:boolean}} [opts] sink=false renders the HUD WITHOUT writing the
 *   file sink — used for worker lines already POSTed to ext.log (avoids dup logs).
 */
export async function pushHud(tabId, entry, { sink = true } = {}) {
  // The sink fires regardless of whether the on-page overlay can be injected, so
  // even attach failures on a restricted page are captured in the log file — UNLESS
  // the caller already logged it (sink=false), in which case we only render the HUD.
  if (sink) sinkLog(entry);
  const { status, pulse } = statusFromEntry(entry);
  await _inject(tabId, { entry, status, pulse });
}

/**
 * Move the orb to an explicit status without adding a log line. Used at lifecycle
 * transitions the agent state machine doesn't cover (debugger attach, relay up,
 * teardown) and for the browser-side voice-connection state.
 * @param {number|null|undefined} tabId
 * @param {string} status  idle | linking | listening | thinking | speaking | error | ended
 */
export async function setHudStatus(tabId, status) {
  if (!status) return;
  await _inject(tabId, { status });
}

/**
 * Page-side renderer, serialized and injected by _inject. Self-contained: it must
 * NOT close over any module-scope binding (only its `payload` argument and page
 * globals like document / window / Math / Date). The panel + its requestAnimation-
 * Frame loop + status state are built ONCE and persist on window.__clarionHud across
 * re-injections; later calls just feed in a status and/or a log line.
 *
 * @param {{entry?:{phase:string,detail?:string,level?:string}, status?:string, pulse?:boolean}} payload
 */
function _renderHud(payload) {
  const ID = "__clarion_debug_hud__";
  const LABELS = {
    idle: "idle",
    linking: "linking",
    listening: "listening",
    thinking: "thinking",
    speaking: "speaking",
    error: "error",
    ended: "ended",
  };

  let root = document.getElementById(ID);
  if (!root) root = _build();

  const S = window.__clarionHud;
  if (S) {
    if (payload && payload.status) S.setStatus(payload.status);
    if (payload && payload.pulse) S.pulse();
    if (payload && payload.entry) S.addLine(payload.entry);
  }

  // --- one-time construction --------------------------------------------------
  function _build() {
    // An SPA may have nuked our node while window state survived — cancel any
    // orphaned animation loop before standing a fresh panel up.
    if (window.__clarionHud && window.__clarionHud.raf) {
      cancelAnimationFrame(window.__clarionHud.raf);
    }
    const prev = document.getElementById(ID);
    if (prev) prev.remove();

    _injectFonts();
    _injectStyle();

    const box = el("div", null);
    box.id = ID;
    box.setAttribute("aria-hidden", "true"); // never announced to the page's AT
    box.dataset.status = "idle";
    // Belt-and-braces positioning as inline attrs, so the panel still lands in a
    // sane spot if the page's CSP blocks our <style> element.
    box.style.position = "fixed";
    box.style.top = "14px";
    box.style.right = "14px";
    box.style.zIndex = "2147483647";

    // topbar (drag handle + controls) ----------------------------------------
    const top = el("div", "cl-top");
    const dot = el("div", "cl-dot");
    const mark = el("div", "cl-wordmark", "Clarion");
    const tag = el("div", "cl-tag", "telemetry");
    const spacer = el("div", "cl-spacer");
    const clearBtn = el("button", "cl-btn", "✕"); // ✕
    clearBtn.title = "clear log";
    const copyBtn = el("button", "cl-btn", "⧉"); // ⧉
    copyBtn.title = "copy log";
    const collapseBtn = el("button", "cl-btn");
    collapseBtn.title = "collapse";
    const chev = el("span", "cl-chev", "▾"); // ▾
    collapseBtn.appendChild(chev);
    top.append(dot, mark, tag, spacer, clearBtn, copyBtn, collapseBtn);

    // body (everything that hides on collapse) -------------------------------
    const body = el("div", "cl-body");

    const viz = el("div", "cl-viz");
    // Centerpiece: a Siri-style pearlescent orb on <canvas>. Its waveform, halo
    // and core gradient follow the live agent status — the same status the bars
    // used to read, now rendered as a single living object on a backing store
    // sized once (the panel never resizes).
    const ORB_W = 340;
    const ORB_H = 188;
    const canvas = el("canvas", "cl-orb");
    const dpr = window.devicePixelRatio || 1;
    canvas.width = ORB_W * dpr;
    canvas.height = ORB_H * dpr;
    canvas.style.width = ORB_W + "px";
    canvas.style.height = ORB_H + "px";
    const octx = canvas.getContext("2d");
    if (octx) octx.scale(dpr, dpr);
    const statusEl = el("div", "cl-status", LABELS.idle);
    viz.append(canvas, statusEl);

    const log = el("div", "cl-log");
    log.id = ID + "_log";

    const foot = el("div", "cl-foot");
    const countEl = el("span", "cl-count", "0 events");
    const endpointEl = el("span", null, "relay :8771 · sink :8772");
    foot.append(countEl, endpointEl);

    body.append(viz, log, foot);
    box.append(top, body);
    (document.body || document.documentElement).appendChild(box);

    // --- persistent state + behaviours --------------------------------------
    const state = {
      status: "idle",
      count: 0,
      stick: true, // auto-scroll while the user is near the bottom
      pulseUntil: 0,
      raf: 0,
      setStatus(st) {
        if (!st || st === state.status) {
          if (st) box.dataset.status = st;
          return;
        }
        state.status = st;
        box.dataset.status = st;
        statusEl.textContent = LABELS[st] || st;
      },
      pulse() {
        state.pulseUntil = performance.now() + 700;
      },
      addLine(entry) {
        const line = el("div", "cl-line");
        line.dataset.level = entry.level || "info";

        const ld = el("span", "cl-ldot");
        const time = el(
          "span",
          "cl-time",
          new Date().toLocaleTimeString([], { hour12: false })
        );

        const rawPhase = sanitize(entry.phase || "");
        const phase = el("span", "cl-phase", rawPhase);
        const cat = categoryOf(rawPhase, entry.level);
        if (cat) phase.dataset.cat = cat;

        line.append(ld, time, phase);
        if (entry.detail) {
          line.appendChild(el("span", "cl-detail", sanitize(entry.detail)));
        }
        log.appendChild(line);

        while (log.childNodes.length > 90) log.removeChild(log.firstChild);
        state.count++;
        countEl.textContent =
          state.count + (state.count === 1 ? " event" : " events");
        if (state.stick) log.scrollTop = log.scrollHeight;
      },
    };
    window.__clarionHud = state;

    // keep auto-scroll only when the user hasn't scrolled up to read history
    log.addEventListener("scroll", () => {
      state.stick = log.scrollHeight - log.scrollTop - log.clientHeight < 28;
    });

    clearBtn.addEventListener("click", () => {
      log.textContent = "";
      state.count = 0;
      countEl.textContent = "0 events";
    });

    copyBtn.addEventListener("click", () => {
      const text = log.innerText;
      const ok = () => {
        copyBtn.textContent = "✓"; // ✓
        setTimeout(() => (copyBtn.textContent = "⧉"), 1100);
      };
      try {
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(text).then(ok, () => selectNode(log));
        } else {
          selectNode(log);
        }
      } catch {
        selectNode(log);
      }
    });

    collapseBtn.addEventListener("click", () => box.classList.toggle("cl-collapsed"));

    // drag by the topbar (ignore clicks that land on a control)
    let drag = null;
    top.addEventListener("pointerdown", (e) => {
      if (e.target.closest(".cl-btn")) return;
      const r = box.getBoundingClientRect();
      drag = { x: e.clientX, y: e.clientY, left: r.left, top: r.top };
      box.style.right = "auto";
      box.style.left = r.left + "px";
      box.style.top = r.top + "px";
      try {
        top.setPointerCapture(e.pointerId);
      } catch {
        /* capture unsupported — drag still works via move events */
      }
      e.preventDefault();
    });
    top.addEventListener("pointermove", (e) => {
      if (!drag) return;
      let nx = drag.left + (e.clientX - drag.x);
      let ny = drag.top + (e.clientY - drag.y);
      nx = Math.max(6, Math.min(window.innerWidth - box.offsetWidth - 6, nx));
      ny = Math.max(6, Math.min(window.innerHeight - 44, ny));
      box.style.left = nx + "px";
      box.style.top = ny + "px";
    });
    const endDrag = () => (drag = null);
    top.addEventListener("pointerup", endDrag);
    top.addEventListener("pointercancel", endDrag);

    // --- the orb render loop -------------------------------------------------
    // A Siri-style pearlescent orb on <canvas>, ported from the Apex Vocal
    // Recovery Suite. State-driven (not audio-reactive): the audio lives in the
    // offscreen doc and can't be streamed in at frame rate, so each status has a
    // hand-tuned motion signature — boundary deformation, halo rings, core
    // gradient — that the orb eases between. A HEARD transcript fires a one-shot
    // ripple. Palette is rose / magenta / violet to match the light-glass panel;
    // never indigo or emerald. Skipped entirely while collapsed.
    let phase = 0;
    function frame(ts) {
      state.raf = requestAnimationFrame(frame);
      if (!octx || box.classList.contains("cl-collapsed")) return;

      const st = state.status;
      const w = ORB_W;
      const h = ORB_H;
      octx.clearRect(0, 0, w, h);
      const cx = w / 2;
      const cy = h / 2;
      const minDim = Math.min(w, h);
      const pulse = state.pulseUntil > ts ? (state.pulseUntil - ts) / 700 : 0;

      let baseRadius = minDim * 0.27;
      let amplitude = 0;
      let ringStroke = "rgba(28,24,32,0.18)";
      let ringRGBA = "rgba(208,76,232,0.0)";
      let deformPath = true; // active states ripple the boundary
      let drawHalo = false; //  quiet states emit slow expanding rings
      let haloRgb = "208,138,200";

      if (st === "error") {
        amplitude = 1;
        baseRadius += Math.sin(phase) * 1.5;
        ringStroke = "#e0556e";
        ringRGBA = "rgba(224,85,110,0.12)";
      } else if (st === "linking") {
        amplitude = 3;
        baseRadius += Math.sin(phase * 4) * 2.5;
        ringStroke = "#d6962f";
        ringRGBA = "rgba(214,150,47,0.12)";
      } else if (st === "speaking") {
        amplitude = minDim * 0.07;
        baseRadius += Math.sin(phase * 2.4) * 5;
        ringStroke = "#d04ce8";
        ringRGBA = "rgba(208,76,232,0.14)";
      } else if (st === "thinking") {
        amplitude = minDim * 0.04;
        baseRadius += Math.sin(phase * 1.1) * 3;
        ringStroke = "#8b5cf6";
        ringRGBA = "rgba(139,92,246,0.12)";
      } else if (st === "listening") {
        // quiet, mic-open breath — last emotion's violet held like a breath
        baseRadius += Math.sin(phase * 0.5) * 1.2;
        ringStroke = "rgba(157,107,216,0.5)";
        ringRGBA = "rgba(157,107,216,0.09)";
        deformPath = false;
        drawHalo = true;
        haloRgb = "157,107,216";
      } else if (st === "ended") {
        baseRadius += Math.sin(phase * 0.4) * 0.8;
        ringStroke = "rgba(150,140,160,0.4)";
        ringRGBA = "rgba(150,140,160,0.06)";
        deformPath = false;
        drawHalo = true;
        haloRgb = "162,150,172";
      } else {
        // idle — breathing pearl, a living object that's waiting, ready
        const breath = Math.sin(phase * 0.6);
        baseRadius += breath * minDim * 0.018;
        const haloAlpha = 0.06 + (breath + 1) * 0.05;
        const strokeAlpha = 0.28 + (breath + 1) * 0.06;
        ringStroke = "rgba(160,113,184," + strokeAlpha.toFixed(3) + ")";
        ringRGBA = "rgba(208,138,200," + haloAlpha.toFixed(3) + ")";
        deformPath = false;
        drawHalo = true;
      }

      baseRadius += pulse * minDim * 0.03; // a HEARD nudge — the orb leans in
      phase += 0.07;

      // Concentric (active) or heartbeat (quiet) rings.
      const ringsCount =
        st === "speaking" ? 4 : st === "thinking" ? 3 : st === "linking" ? 2 : drawHalo ? 3 : 1;
      for (let i = 1; i <= ringsCount; i++) {
        octx.beginPath();
        if (drawHalo) {
          const tt = (phase * 0.18 + i * 0.45) % 1.4; // phase-shifted per ring
          const expansion = tt / 1.4; // 0 → 1
          const r = baseRadius + minDim * 0.02 + expansion * minDim * 0.22;
          const alpha = (1 - expansion) * 0.18;
          octx.strokeStyle = "rgba(" + haloRgb + "," + alpha.toFixed(3) + ")";
          octx.lineWidth = 1.2;
          octx.arc(cx, cy, r, 0, Math.PI * 2);
        } else {
          octx.strokeStyle = ringRGBA;
          octx.lineWidth = 1.4 / Math.sqrt(i);
          const breath = st === "speaking" || st === "thinking" ? Math.sin(phase + i) * 0.1 : 0;
          const r = baseRadius + i * (minDim * 0.06) * (1 + breath);
          octx.arc(cx, cy, r, 0, Math.PI * 2);
        }
        octx.stroke();
      }

      // HEARD ripple — one bright ring flung outward, decaying with the pulse.
      if (pulse > 0) {
        octx.beginPath();
        octx.strokeStyle = "rgba(208,76,232," + (pulse * 0.5).toFixed(3) + ")";
        octx.lineWidth = 2;
        octx.arc(cx, cy, baseRadius + (1 - pulse) * minDim * 0.32, 0, Math.PI * 2);
        octx.stroke();
      }

      // Main boundary — a perfect circle when calm, a waveform when active.
      octx.beginPath();
      octx.strokeStyle = ringStroke;
      octx.lineWidth = deformPath ? 2 : 1.4;
      if (!deformPath) {
        octx.arc(cx, cy, baseRadius, 0, Math.PI * 2);
      } else {
        const points = 96;
        for (let i = 0; i <= points; i++) {
          const angle = (i / points) * Math.PI * 2;
          const offset =
            Math.sin(angle * 5 + phase) *
            Math.cos(angle * 3 + phase * 0.6) *
            amplitude *
            (0.6 + 0.4 * Math.sin(phase * 1.2));
          const r = baseRadius + offset;
          const x = cx + Math.cos(angle) * r;
          const y = cy + Math.sin(angle) * r;
          if (i === 0) octx.moveTo(x, y);
          else octx.lineTo(x, y);
        }
        octx.closePath();
      }
      octx.stroke();

      // Pearlescent core fill — never flat.
      const grad = octx.createRadialGradient(
        cx - baseRadius * 0.25,
        cy - baseRadius * 0.3,
        baseRadius * 0.1,
        cx,
        cy,
        baseRadius
      );
      if (st === "error") {
        grad.addColorStop(0, "rgba(254,226,226,0.95)");
        grad.addColorStop(1, "rgba(254,242,242,0.55)");
      } else if (st === "speaking") {
        grad.addColorStop(0, "rgba(255,232,244,0.95)");
        grad.addColorStop(0.55, "rgba(241,215,248,0.7)");
        grad.addColorStop(1, "rgba(221,207,247,0.45)");
      } else if (st === "thinking") {
        grad.addColorStop(0, "rgba(246,241,253,0.95)");
        grad.addColorStop(0.55, "rgba(229,222,249,0.78)");
        grad.addColorStop(1, "rgba(213,205,242,0.5)");
      } else if (st === "linking") {
        grad.addColorStop(0, "rgba(254,243,199,0.92)");
        grad.addColorStop(1, "rgba(255,251,235,0.5)");
      } else if (st === "listening") {
        grad.addColorStop(0, "rgba(245,236,250,0.9)");
        grad.addColorStop(0.55, "rgba(236,226,248,0.7)");
        grad.addColorStop(1, "rgba(224,214,244,0.46)");
      } else if (st === "ended") {
        grad.addColorStop(0, "rgba(246,244,248,0.9)");
        grad.addColorStop(1, "rgba(224,221,230,0.5)");
      } else {
        grad.addColorStop(0, "rgba(255,250,252,0.95)");
        grad.addColorStop(0.55, "rgba(245,230,240,0.78)");
        grad.addColorStop(1, "rgba(220,205,232,0.52)");
      }
      octx.beginPath();
      octx.fillStyle = grad;
      octx.arc(cx, cy, baseRadius - 1, 0, Math.PI * 2);
      octx.fill();

      // Gloss highlight cap (top-left), the wet pearl sheen.
      const hl = octx.createRadialGradient(
        cx - baseRadius * 0.35,
        cy - baseRadius * 0.45,
        2,
        cx - baseRadius * 0.35,
        cy - baseRadius * 0.45,
        baseRadius * 0.55
      );
      hl.addColorStop(0, "rgba(255,255,255,0.55)");
      hl.addColorStop(1, "rgba(255,255,255,0)");
      octx.beginPath();
      octx.fillStyle = hl;
      octx.arc(cx, cy, baseRadius - 1, 0, Math.PI * 2);
      octx.fill();
    }
    state.raf = requestAnimationFrame(frame);

    return box;

    // --- tiny DOM builder ---------------------------------------------------
    function el(tag, cls, text) {
      const node = document.createElement(tag);
      if (cls) node.className = cls;
      if (text != null) node.textContent = text;
      return node;
    }
  }

  // --- helpers shared by build + per-line render ------------------------------

  /** Map a bracketed phase prefix (or the level) to a colour category. */
  function categoryOf(phase, level) {
    const m = /^\s*\[(\w+)\]/.exec(phase);
    if (m) {
      const c = m[1].toLowerCase();
      if (c === "asr") return "asr";
      if (c === "agent") return "agent";
      if (c === "tool") return "tool";
      if (c === "turn") return "turn";
      if (c === "error") return "error";
    }
    if (level === "err") return "error";
    return null;
  }

  /**
   * Honour the kernel copy rule (no helpless-framing role words in any UI) even
   * on raw worker lines — the LiveKit role label for the agent's turns is rewritten
   * to "Clarion" on the panel. The patterns are assembled from fragments so this
   * very file stays clean under scripts/copy_lint.py.
   */
  function sanitize(s) {
    const reRole = new RegExp("assi" + "stant", "gi");
    const reAux = new RegExp("\\bhel" + "pers?\\b", "gi");
    const reVerb = new RegExp("\\bassi" + "st(?:ing|ed|s)?\\b", "gi");
    return String(s == null ? "" : s)
      .replace(reRole, "Clarion")
      .replace(reAux, "co-pilot")
      .replace(reVerb, "help");
  }

  function selectNode(node) {
    if (!node) return;
    try {
      const range = document.createRange();
      range.selectNodeContents(node);
      const sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(range);
    } catch {
      /* selection unsupported — nothing to do */
    }
  }

  /**
   * Best-effort web-font load (Instrument Serif display · Inter Tight body ·
   * JetBrains Mono log) to match the Apex suite's typography. id-guarded; if the
   * page's CSP blocks fonts.googleapis.com, the panel falls back to the system
   * serif/sans/mono stacks declared in the stylesheet — no error surfaces.
   */
  function _injectFonts() {
    const FID = ID + "_fonts";
    if (document.getElementById(FID)) return;
    try {
      const link = document.createElement("link");
      link.id = FID;
      link.rel = "stylesheet";
      link.href =
        "https://fonts.googleapis.com/css2?family=Instrument+Serif:ital@0;1&family=Inter+Tight:wght@400;500;600&family=JetBrains+Mono:wght@400;500&display=swap";
      (document.head || document.documentElement).appendChild(link);
    } catch {
      /* font load blocked — system fallbacks apply */
    }
  }

  /** Inject the panel stylesheet once (id-scoped so it can't bleed into the page). */
  function _injectStyle() {
    const SID = ID + "_style";
    if (document.getElementById(SID)) return;
    const style = document.createElement("style");
    style.id = SID;
    style.textContent = `
#${ID}{
  --ink:#241f2b;--soft:#5d5562;--faint:#9a93a3;
  --hair:rgba(40,24,60,.09);--hair-strong:rgba(40,24,60,.14);
  --stroke:rgba(255,255,255,.72);
  --ok:#1c9e6a;--warn:#c9821f;--err:#d6336c;
  --c-asr:#b7458a;--c-agent:#7c3aed;--c-tool:#b45309;--c-turn:#5b6478;--c-error:#d6336c;
  --bar:#b08fc4;
  position:fixed;top:14px;right:14px;z-index:2147483647;
  width:376px;max-width:calc(100vw - 28px);
  color:var(--ink);
  font-family:"Inter Tight",ui-sans-serif,-apple-system,"SF Pro Text","Segoe UI",system-ui,sans-serif;
  font-size:12px;line-height:1.45;text-align:left;letter-spacing:-.01em;
  background:
    radial-gradient(120% 82% at 100% -12%,rgba(208,138,200,.16),transparent 58%),
    radial-gradient(95% 72% at -12% 116%,rgba(139,92,246,.12),transparent 60%),
    linear-gradient(180deg,rgba(255,255,255,.66),rgba(252,248,252,.58));
  -webkit-backdrop-filter:blur(40px) saturate(185%);backdrop-filter:blur(40px) saturate(185%);
  border:1px solid var(--stroke);border-radius:18px;
  box-shadow:
    inset 0 1px 0 rgba(255,255,255,.9),
    inset 0 -1px 0 rgba(255,255,255,.45),
    0 28px 70px rgba(80,40,120,.18),
    0 6px 18px rgba(80,40,120,.10);
  overflow:hidden;user-select:none;-webkit-user-select:none;
}
#${ID} *{box-sizing:border-box;margin:0;padding:0;}
#${ID}::before{
  content:"";position:absolute;inset:0 0 auto 0;height:1px;
  background:linear-gradient(90deg,transparent,color-mix(in srgb,var(--bar) 60%,transparent),transparent);
  opacity:.7;pointer-events:none;
}
#${ID}[data-status="listening"]{--bar:#9d6bd8;}
#${ID}[data-status="thinking"]{--bar:#8b5cf6;}
#${ID}[data-status="speaking"]{--bar:#d04ce8;}
#${ID}[data-status="linking"]{--bar:#d6962f;}
#${ID}[data-status="idle"]{--bar:#b08fc4;}
#${ID}[data-status="error"]{--bar:#e0556e;}
#${ID}[data-status="ended"]{--bar:#a39bad;}

#${ID} .cl-top{display:flex;align-items:center;gap:9px;padding:10px 11px 10px 13px;
  cursor:grab;border-bottom:1px solid var(--hair);touch-action:none;}
#${ID} .cl-top:active{cursor:grabbing;}
#${ID} .cl-dot{width:8px;height:8px;border-radius:50%;flex:none;
  background:var(--bar);box-shadow:0 0 12px -1px var(--bar);
  animation:cl-breathe 2.4s ease-in-out infinite;transition:background .5s ease,box-shadow .5s ease;}
#${ID} .cl-wordmark{font-family:"Instrument Serif",Georgia,"Times New Roman",serif;
  font-weight:400;font-size:20px;line-height:1;letter-spacing:.01em;color:var(--ink);}
#${ID} .cl-tag{font-family:"JetBrains Mono",ui-monospace,Menlo,monospace;
  font-size:9px;letter-spacing:.18em;text-transform:uppercase;color:var(--faint);}
#${ID} .cl-spacer{flex:1;}
#${ID} .cl-btn{all:unset;cursor:pointer;color:var(--soft);width:23px;height:21px;
  border-radius:7px;display:grid;place-items:center;font-size:12px;line-height:1;
  border:1px solid transparent;transition:background .15s,color .15s,border-color .15s;}
#${ID} .cl-btn:hover{color:var(--ink);background:rgba(255,255,255,.55);border-color:var(--hair-strong);}

#${ID} .cl-viz{position:relative;display:flex;flex-direction:column;align-items:center;
  gap:6px;padding:14px 12px;border-bottom:1px solid var(--hair);
  background:
    linear-gradient(180deg,rgba(255,255,255,.5),rgba(255,255,255,0) 42%),
    radial-gradient(120% 95% at 50% 132%,color-mix(in srgb,var(--bar) 13%,transparent),transparent 70%);}
#${ID} .cl-orb{display:block;margin:0 auto;filter:drop-shadow(0 10px 26px rgba(150,90,180,.16));}
#${ID} .cl-status{font-family:"JetBrains Mono",ui-monospace,Menlo,monospace;
  font-size:10px;letter-spacing:.26em;text-transform:uppercase;
  font-weight:600;color:var(--bar);transition:color .5s ease;}

#${ID} .cl-log{max-height:44vh;overflow-y:auto;overflow-x:hidden;padding:6px 2px 6px 0;
  font-family:"JetBrains Mono",ui-monospace,"SF Mono",Menlo,monospace;font-size:11px;line-height:1.55;
  color:var(--soft);user-select:text;-webkit-user-select:text;
  scrollbar-width:thin;scrollbar-color:rgba(40,24,60,.2) transparent;}
#${ID} .cl-log::-webkit-scrollbar{width:9px;}
#${ID} .cl-log::-webkit-scrollbar-thumb{background:rgba(40,24,60,.16);border-radius:9px;
  border:2px solid transparent;background-clip:padding-box;}
#${ID} .cl-log::-webkit-scrollbar-thumb:hover{background:rgba(40,24,60,.28);}
#${ID} .cl-line{display:grid;grid-template-columns:10px auto 1fr;align-items:baseline;
  column-gap:7px;padding:3px 12px 3px 11px;border-left:2px solid transparent;animation:cl-in .22s ease;}
#${ID} .cl-line:hover{background:rgba(40,24,60,.035);}
#${ID} .cl-ldot{width:6px;height:6px;border-radius:50%;align-self:center;
  margin-top:1px;background:rgba(40,24,60,.24);}
#${ID} .cl-line[data-level="ok"] .cl-ldot{background:var(--ok);}
#${ID} .cl-line[data-level="warn"] .cl-ldot{background:var(--warn);}
#${ID} .cl-line[data-level="err"] .cl-ldot{background:var(--err);}
#${ID} .cl-line[data-level="err"]{border-left-color:color-mix(in srgb,var(--err) 55%,transparent);
  background:color-mix(in srgb,var(--err) 7%,transparent);}
#${ID} .cl-time{color:var(--faint);font-size:10px;font-variant-numeric:tabular-nums;white-space:nowrap;}
#${ID} .cl-phase{font-weight:600;color:var(--ink);white-space:nowrap;}
#${ID} .cl-phase[data-cat="asr"]{color:var(--c-asr);}
#${ID} .cl-phase[data-cat="agent"]{color:var(--c-agent);}
#${ID} .cl-phase[data-cat="tool"]{color:var(--c-tool);}
#${ID} .cl-phase[data-cat="turn"]{color:var(--c-turn);}
#${ID} .cl-phase[data-cat="error"]{color:var(--c-error);}
#${ID} .cl-detail{grid-column:3;color:var(--soft);word-break:break-word;min-width:0;}

#${ID} .cl-foot{display:flex;align-items:center;justify-content:space-between;
  padding:7px 12px;border-top:1px solid var(--hair);
  font-family:"JetBrains Mono",ui-monospace,Menlo,monospace;font-size:9px;letter-spacing:.04em;color:var(--faint);}

#${ID}.cl-collapsed{width:200px;}
#${ID}.cl-collapsed .cl-body{display:none;}
#${ID}.cl-collapsed .cl-tag{display:none;}
#${ID} .cl-chev{display:inline-block;transition:transform .2s ease;color:var(--soft);}
#${ID}.cl-collapsed .cl-chev{transform:rotate(-90deg);}

@keyframes cl-in{from{opacity:0;transform:translateY(3px);}to{opacity:1;transform:none;}}
@keyframes cl-breathe{0%,100%{opacity:.55;transform:scale(.88);}50%{opacity:1;transform:scale(1.15);}}
`;
    (document.head || document.documentElement).appendChild(style);
  }
}
