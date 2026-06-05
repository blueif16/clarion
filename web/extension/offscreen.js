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

  const r = new LK.Room({
    // Voice is the only medium; bias capture for speech intelligibility.
    audioCaptureDefaults: {
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
    });
  } else if (msg.type === MSG.DISCONNECT) {
    disconnect();
  }
});

// Announce readiness so the worker may (re)send config it queued before this
// document finished loading.
reportState("ready");
