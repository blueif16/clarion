// config.example.js — template for the browser-side voice config.
//
// Copy this to  config.js  (which is gitignored) and fill in real values. The
// service worker dynamically imports config.js on session start to join the
// LiveKit room. If config.js is absent, voice is skipped (the CDP relay still
// works). NEVER commit a real token or secret — config.js is gitignored.
//
// LIVEKIT_URL  — your LiveKit Cloud / self-hosted signalling URL (wss://...).
//                Matches LIVEKIT_URL in agent/.env (same project the Python
//                voice_entry worker connects to).
// ROOM_NAME    — the room the human joins. It MUST be the room the Python worker
//                is dispatched into (see the README for how the worker's room is
//                chosen). The extension joins as the human participant; the
//                Python worker joins as the agent.
// TOKEN        — a LiveKit access token for the human participant, scoped to
//                ROOM_NAME with canPublish + canSubscribe. Mint it with the
//                LiveKit CLI (`lk token create`) using the API key/secret from
//                agent/.env — see the README "Mint a participant token" section.
//                Tokens expire; regenerate when joining.
// MIC_MATCH    — OPTIONAL. A label substring (e.g. "MacBook Pro Microphone") to
//                force a specific input device. Leave empty to let the offscreen
//                doc AUTO-PREFER a real mic over virtual devices (MMAudio, Teams,
//                loopbacks…) — those are often the OS default and capture silence.
// MIC_DEVICE_ID — OPTIONAL. An exact deviceId (overrides MIC_MATCH).

export default {
  LIVEKIT_URL: "wss://YOUR-PROJECT.livekit.cloud",
  ROOM_NAME: "clarion-hero",
  TOKEN: "REPLACE_WITH_A_FRESH_PARTICIPANT_TOKEN",
  MIC_MATCH: "",
};
