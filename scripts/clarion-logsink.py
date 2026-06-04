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

Run:  .venv/bin/python scripts/clarion-logsink.py     (port 8772; Ctrl-C to stop)
Read: tail -f /tmp/clarion-ext.log
"""
from __future__ import annotations

import datetime
import http.server
import sys

LOG_PATH = "/tmp/clarion-ext.log"
PORT = 8772


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
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {body}"
        with open(LOG_PATH, "a") as f:
            f.write(line + "\n")
        print(line, flush=True)
        self.send_response(204)
        self._cors()
        self.end_headers()

    def log_message(self, *args) -> None:  # silence the default access log
        pass


def main() -> int:
    print(f"== clarion-logsink on http://127.0.0.1:{PORT} → {LOG_PATH} ==", flush=True)
    print("   (extension POSTs here; `tail -f {0}` to read)".format(LOG_PATH), flush=True)
    server = http.server.HTTPServer(("127.0.0.1", PORT), _Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n   logsink stopped.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
