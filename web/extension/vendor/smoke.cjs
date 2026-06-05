// vendor/smoke.cjs — reproducible parse/symbol check for the vendored UMD.
//
// Run:  node web/extension/vendor/smoke.cjs        (exit 0 = PASS, 1 = FAIL)
//
// The browser loads vendor/livekit-client.umd.js as a CLASSIC <script>, where
// it assigns globalThis.LivekitClient. Under Node we instead load it via the
// CommonJS loader (the UMD's exports/module branch) and assert it exposes the
// exact symbols offscreen.js depends on: Room (+ instance connect/disconnect/
// setMicrophoneEnabled), RoomEvent.TrackSubscribed, Track.Kind.Audio.
//
// NOTE: a plain `require()` of this file from inside web/extension/ would fail
// because that directory's package.json declares `"type":"module"`, so Node
// would parse the .js as ESM and hand back an empty namespace. We sidestep that
// by compiling the source as CommonJS explicitly.
const fs = require("fs");
const Module = require("module");
const path = require("path");

const file =
  process.argv[2] || path.join(__dirname, "livekit-client.umd.js");
const code = fs.readFileSync(file, "utf8");

const m = new Module(file, null);
m.filename = file;
m.paths = Module._nodeModulePaths(path.dirname(file));
m._compile(code, file); // runs the UMD with a real CommonJS module/exports

const lk = m.exports;
const room = new lk.Room();

const checks = {
  "exports is object": typeof lk === "object" && lk !== null,
  "Room is constructor": typeof lk.Room === "function",
  "RoomEvent.TrackSubscribed === 'trackSubscribed'":
    lk.RoomEvent && lk.RoomEvent.TrackSubscribed === "trackSubscribed",
  "Track.Kind.Audio === 'audio'":
    lk.Track && lk.Track.Kind && lk.Track.Kind.Audio === "audio",
  "version is 2.x": typeof lk.version === "string" && lk.version.startsWith("2."),
  "new Room().connect is fn": typeof room.connect === "function",
  "new Room().disconnect is fn": typeof room.disconnect === "function",
  "localParticipant.setMicrophoneEnabled is fn":
    room.localParticipant &&
    typeof room.localParticipant.setMicrophoneEnabled === "function",
};

let allOk = true;
for (const [name, ok] of Object.entries(checks)) {
  console.log((ok ? "ok  " : "FAIL") + "  " + name);
  if (!ok) allOk = false;
}
console.log("version:", lk.version);
console.log(allOk ? "SMOKE: PASS" : "SMOKE: FAIL");
process.exit(allOk ? 0 : 1);
