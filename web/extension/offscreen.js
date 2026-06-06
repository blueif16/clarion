// offscreen.js — Clarion browser-side voice, run inside the offscreen document.
//
// Role: hold the long-lived LiveKit/WebRTC connection that the MV3 service
// worker cannot (the worker dies at ~30s idle; an offscreen document does not).
// On a `voice.connect` message from the worker it joins the SAME LiveKit room
// the unchanged Python `voice_entry` worker is on, publishes the user's mic, and
// plays the agent's incoming audio track. On `voice.disconnect` it leaves.
//
// This is a SEPARATE connection from the CDP relay WebSocket (which stays in the
// service worker). Voice is purely additive.
//
// The vendored LiveKit SDK is loaded by offscreen.html as a classic script and
// exposed as globalThis.LivekitClient — MV3 forbids remote code, so it is local.

const LK = globalThis.LivekitClient;

// Message types on the chrome.runtime channel between the SW and this document.
// Kept in sync with the constants in service-worker.js.
const MSG = {
  CONNECT: "voice.connect", // SW → offscreen: {target, livekitUrl, token, roomName}
  DISCONNECT: "voice.disconnect", // SW → offscreen: {target}
  STATE: "voice.state", // offscreen → SW: {target, state, detail?}
};
const OFFSCREEN_TARGET = "offscreen-voice";
const SW_TARGET = "service-worker-voice";

/** @type {import('livekit-client').Room | null} */
let room = null;
let connecting = false;

/**
 * Report a connection-state transition back to the service worker. Best-effort:
 * the worker may be asleep, in which case the send simply has no receiver.
 * @param {string} state  one of: connecting | connected | disconnected | error
 * @param {string} [detail]
 */
function reportState(state, detail) {
  try {
    chrome.runtime.sendMessage({
      type: MSG.STATE,
      target: SW_TARGET,
      state,
      detail: detail == null ? "" : String(detail),
    });
  } catch {
    /* no receiver (worker asleep) — non-fatal */
  }
}

/**
 * Forward a diagnostic line to the service worker so it lands on the on-page HUD
 * and /tmp/clarion-ext.log. Best-effort: a sleeping worker just drops it.
 */
function swLog(phase, detail, level) {
  try {
    chrome.runtime.sendMessage({
      type: "voice.log",
      target: SW_TARGET,
      phase,
      detail: detail == null ? "" : String(detail),
      level: level || "info",
    });
  } catch {
    /* no receiver — non-fatal */
  }
}

// Labels that are NOT a real microphone: virtual routers, loopbacks, meeting-app
// devices. The macOS DEFAULT input is often one of these (e.g. "MMAudio Device"),
// which is exactly why trusting the OS default captures silence.
const VIRTUAL_MIC_RE =
  /mmaudio|virtual|loopback|black ?hole|soundflower|vb[- ]?(audio|cable)|aggregate|multi-?output|stereo mix|\bcable\b|teams|zoom|oray|webex|krisp|voicemeeter/i;

/**
 * Choose a REAL microphone instead of trusting the OS default. Order:
 *   explicit deviceId → label substring match → first non-virtual (prefer
 *   built-in) → null (caller falls back to the OS default).
 * @returns {Promise<{device: MediaDeviceInfo|null, all: MediaDeviceInfo[]}>}
 */
async function pickMicrophone(micDeviceId, micMatch) {
  let mics = [];
  try {
    mics = await LK.Room.getLocalDevices("audioinput");
  } catch (e) {
    swLog("mic: enumerate FAILED", e && e.message, "err");
    return { device: null, all: [] };
  }
  // Drop the synthetic 'default'/'communications' aliases when choosing.
  const real = mics.filter(
    (d) => d.deviceId && d.deviceId !== "default" && d.deviceId !== "communications"
  );
  let chosen = null;
  if (micDeviceId) chosen = real.find((d) => d.deviceId === micDeviceId) || null;
  if (!chosen && micMatch) {
    const m = String(micMatch).toLowerCase();
    chosen = real.find((d) => (d.label || "").toLowerCase().includes(m)) || null;
  }
  if (!chosen) {
    const realMics = real.filter((d) => !VIRTUAL_MIC_RE.test(d.label || ""));
    chosen =
      realMics.find((d) => /built-?in|macbook|internal|microphone array/i.test(d.label || "")) ||
      realMics[0] ||
      null;
  }
  return { device: chosen, all: mics };
}

/**
 * After the mic is published, log the ACTUAL captured device and start a short
 * signal monitor so a wrong/silent device is visible: it logs the first real
 * audio ("AUDIO DETECTED ✓") or a "NO AUDIO" warning after ~10s.
 */
function logActiveMicAndMonitor(r) {
  let mst = null;
  try {
    const pub = r.localParticipant.getTrackPublication(LK.Track.Source.Microphone);
    mst = pub && pub.track && pub.track.mediaStreamTrack;
  } catch {
    /* track shape differs — fall through */
  }
  if (!mst) {
    swLog("mic: no published track to monitor", "", "warn");
    return;
  }
  const s = (mst.getSettings && mst.getSettings()) || {};
  swLog("mic: capturing", `${mst.label || "?"}${s.deviceId ? " (" + String(s.deviceId).slice(0, 8) + "…)" : ""}`, "ok");
  try {
    const AC = self.AudioContext || self.webkitAudioContext;
    const ctx = new AC();
    const src = ctx.createMediaStreamSource(new MediaStream([mst]));
    const an = ctx.createAnalyser();
    an.fftSize = 512;
    src.connect(an);
    const buf = new Uint8Array(an.fftSize);
    let ticks = 0;
    const timer = setInterval(() => {
      an.getByteTimeDomainData(buf);
      let peak = 0;
      for (let i = 0; i < buf.length; i++) peak = Math.max(peak, Math.abs(buf[i] - 128));
      const level = peak / 128;
      ticks++;
      if (level > 0.02) {
        swLog("mic: AUDIO DETECTED ✓", `level≈${level.toFixed(2)} — the ASR is receiving your voice`, "ok");
        clearInterval(timer);
        try { ctx.close(); } catch {}
      } else if (ticks >= 40) {
        // ~10s of silence at 250ms ticks
        swLog("mic: NO AUDIO after 10s ✗", "wrong/virtual device or muted — check System Settings → Sound → Input", "err");
        clearInterval(timer);
        try { ctx.close(); } catch {}
      }
    }, 250);
  } catch (e) {
    swLog("mic: level monitor unavailable", e && e.message, "warn");
  }
}

/** Attach a freshly subscribed remote audio track to the <audio> sink. */
function attachAgentAudio(track) {
  const sink = /** @type {HTMLAudioElement} */ (
    document.getElementById("agent-audio")
  );
  // track.attach(el) routes the MediaStreamTrack into the existing element so
  // the autoplay sink plays the agent's voice.
  track.attach(sink);
}

/**
 * Join the LiveKit room, publish the mic, and play remote audio.
 * @param {{livekitUrl:string, token:string, roomName?:string}} cfg
 */
async function connect(cfg) {
  if (!LK || typeof LK.Room !== "function") {
    reportState("error", "vendored livekit-client not loaded");
    return;
  }
  if (room || connecting) {
    // Already live (or mid-connect): treat connect as idempotent.
    return;
  }
  if (!cfg || !cfg.livekitUrl || !cfg.token) {
    reportState("error", "missing livekitUrl or token");
    return;
  }

  connecting = true;
  reportState("connecting");

  // Pick a REAL mic (not the OS default, which is often a virtual device) and
  // log every input device so a wrong capture source is visible immediately.
  const { device: mic, all: allMics } = await pickMicrophone(cfg.micDeviceId, cfg.micMatch);
  const listed = allMics
    .map((d) => (d.deviceId === "default" ? "[default] " : "") + (d.label || "(no label)"))
    .join(" · ");
  swLog("mic: input devices", listed || "(none — was mic permission granted?)");
  if (mic) swLog("mic: selected", mic.label, "ok");
  else swLog("mic: NO real device — using OS default (may be a virtual device!)", "", "warn");

  const r = new LK.Room({
    // Voice is the only medium; bias capture for speech intelligibility.
    audioCaptureDefaults: {
      // Force the chosen real device; undefined → browser default (last resort).
      deviceId: mic ? mic.deviceId : undefined,
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    },
  });

  // Wire handlers BEFORE connect so no early track subscription is missed.
  r.on(LK.RoomEvent.TrackSubscribed, (track) => {
    if (track.kind === LK.Track.Kind.Audio) attachAgentAudio(track);
  });
  r.on(LK.RoomEvent.TrackUnsubscribed, (track) => {
    try {
      track.detach();
    } catch {
      /* already detached */
    }
  });
  r.on(LK.RoomEvent.Disconnected, (reason) => {
    room = null;
    reportState("disconnected", reason == null ? "" : String(reason));
  });

  try {
    await r.connect(cfg.livekitUrl, cfg.token);
    // Publish the user's mic. The browser permission was already granted from
    // the full-tab request-mic page (getUserMedia cannot prompt in here), so
    // this resolves without a prompt.
    await r.localParticipant.setMicrophoneEnabled(true);
    // Log the ACTUAL captured device + start the signal monitor (audio-detected
    // / no-audio), so "is the ASR hearing me?" is answered on the HUD.
    logActiveMicAndMonitor(r);
    room = r;
    connecting = false;
    reportState("connected", r.name || (cfg.roomName || ""));
  } catch (err) {
    connecting = false;
    try {
      await r.disconnect();
    } catch {
      /* nothing to tear down */
    }
    reportState("error", err && err.message ? err.message : String(err));
  }
}

/** Leave the room cleanly (idempotent). */
async function disconnect() {
  connecting = false;
  const r = room;
  room = null;
  if (!r) return;
  try {
    await r.disconnect();
  } catch {
    /* already gone */
  }
}

chrome.runtime.onMessage.addListener((msg) => {
  if (!msg || msg.target !== OFFSCREEN_TARGET) return;
  if (msg.type === MSG.CONNECT) {
    connect({
      livekitUrl: msg.livekitUrl,
      token: msg.token,
      roomName: msg.roomName,
      micDeviceId: msg.micDeviceId,
      micMatch: msg.micMatch,
    });
  } else if (msg.type === MSG.DISCONNECT) {
    disconnect();
  }
});

// Announce readiness so the worker may (re)send config it queued before this
// document finished loading.
reportState("ready");
