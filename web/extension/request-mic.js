// request-mic.js — runs in a FULL extension tab (request-mic.html).
//
// Why a full tab: getUserMedia() CANNOT prompt for the microphone from inside an
// offscreen document, side panel, or popup — only a top-level extension page can
// surface the permission prompt. Once the user grants here, Chrome remembers the
// grant for the extension origin, so the offscreen document can later open the
// mic without prompting. The service worker opens this tab when the grant is not
// yet in place, then the offscreen voice document does the actual capture.
//
// This page requests the mic ONCE, reports grant/deny to the worker, and (on a
// grant) closes itself so the flow is a single click.

const MSG = {
  MIC_RESULT: "voice.mic-result", // request-mic → SW: {target, granted, detail?}
};
const SW_TARGET = "service-worker-voice";

const statusEl = document.getElementById("status");
const retryEl = document.getElementById("retry");

function setStatus(text, kind) {
  statusEl.textContent = text;
  statusEl.className = kind || "";
}

function report(granted, detail) {
  try {
    chrome.runtime.sendMessage({
      type: MSG.MIC_RESULT,
      target: SW_TARGET,
      granted: !!granted,
      detail: detail == null ? "" : String(detail),
    });
  } catch {
    /* worker may be asleep; the grant itself is what persists */
  }
}

async function requestMic() {
  retryEl.hidden = true;
  setStatus("Requesting microphone…");
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    // Release the device immediately — we only needed the grant, the offscreen
    // document opens its own capture for the room.
    for (const track of stream.getTracks()) track.stop();
    setStatus("Microphone granted. You can close this tab.", "ok");
    report(true);
    // Self-close shortly after a grant so the flow is one tap. window.close()
    // works for a tab the extension itself opened.
    setTimeout(() => {
      try {
        window.close();
      } catch {
        /* user can close it manually */
      }
    }, 900);
  } catch (err) {
    const detail = err && err.message ? err.message : String(err);
    setStatus("Microphone was not granted: " + detail, "err");
    report(false, detail);
    retryEl.hidden = false;
  }
}

retryEl.addEventListener("click", requestMic);

// Kick off on load.
requestMic();
