#!/usr/bin/env python3
"""clarion-logsink.py — durable browser-log sink so the agent (and you) can READ
the extension's logs directly, instead of copy-pasting from DevTools.

The MV3 extension can't write files, so its service-worker + HUD logs are trapped
in Chrome's sandbox. This tiny always-on HTTP server receives the lines the
extension POSTs (see hud.js `sinkLog`) and appends them, timestamped, to
/tmp/clarion-ext.log — which anyone can `tail -f`.

It is ALWAYS-ON and independent of the CDP relay / voice worker, so it captures
even pre-attach failures (e.g. "attach FAILED — close DevTools"). The extension
POSTs `text/plain` (a CORS "simple" request) so there is no preflight.

It ALSO exposes a tiny control endpoint the extension can't otherwise reach:
  POST /reset-room  (body = room name; falls back to CLARION_ROOM / "clarion-hero")
    Deletes the LiveKit room so the next join CREATES it fresh. The voice worker's
    automatic dispatch fires only on room CREATION, so a room left over from a
    prior run (empty_timeout 300s) gets NO agent → silent voice. The HUD refresh
    button calls this so "restart fresh" actually re-dispatches a fresh agent.

Run:  .venv/bin/python scripts/clarion-logsink.py     (port 8772; Ctrl-C to stop)
Read: tail -f /tmp/clarion-ext.log
"""
from __future__ import annotations

import asyncio
import datetime
import http.server
import os
import sys

LOG_PATH = "/tmp/clarion-ext.log"
PORT = 8772
# agent/.env sits next to this scripts/ dir; load it so api.LiveKitAPI() finds the
# LIVEKIT_URL/API_KEY/API_SECRET creds the room-control endpoint needs.
ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "agent", ".env")


def _load_env() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv(ENV_PATH)
    except Exception:  # noqa: BLE001 - logging still works without creds
        pass


def _default_room() -> str:
    return os.environ.get("CLARION_ROOM", "clarion-hero")


def _delete_room(name: str) -> None:
    """Delete a LiveKit room (idempotent: a missing room is treated as success).
    Synchronous wrapper around the async API for the blocking HTTP handler."""
    from livekit import api

    async def _go() -> None:
        lk = api.LiveKitAPI()
        try:
            await lk.room.delete_room(api.DeleteRoomRequest(room=name))
        finally:
            await lk.aclose()

    asyncio.run(_go())


def _write(line: str) -> None:
    with open(LOG_PATH, "a") as f:
        f.write(line + "\n")
    print(line, flush=True)


class _Handler(http.server.BaseHTTPRequestHandler):
    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")

    def do_OPTIONS(self) -> None:  # noqa: N802 - defensive (text/plain skips this)
        self.send_response(204)
        self._cors()
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        n = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(n).decode("utf-8", "replace").strip() if n else ""
        if self.path.rstrip("/") == "/reset-room":
            self._reset_room(body)
            return
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        _write(f"[{ts}] {body}")
        self.send_response(204)
        self._cors()
        self.end_headers()

    def _reset_room(self, body: str) -> None:
        """Delete the room so the next join recreates it → the agent re-dispatches."""
        room = body or _default_room()
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        try:
            _delete_room(room)
            _write(f"[{ts}] OK | reset-room | deleted {room!r} (next join recreates it → agent re-dispatches)")
            status = 200
        except Exception as e:  # noqa: BLE001 - surface the failure to the ext log
            _write(f"[{ts}] ERR | reset-room FAILED | {room!r}: {e}")
            status = 500
        self.send_response(status)
        self._cors()
        self.end_headers()

    def log_message(self, *args) -> None:  # silence the default access log
        pass


def main() -> int:
    _load_env()
    print(f"== clarion-logsink on http://127.0.0.1:{PORT} → {LOG_PATH} ==", flush=True)
    print("   (extension POSTs lines here; POST /reset-room deletes the LiveKit room)", flush=True)
    print(f"   tail -f {LOG_PATH} to read", flush=True)
    server = http.server.HTTPServer(("127.0.0.1", PORT), _Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n   logsink stopped.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
