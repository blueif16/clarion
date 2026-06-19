#!/usr/bin/env bash
# clarion-status.sh — one command to see the live state of the Clarion stack.
#
# Run this FIRST each session (or after anything weird) to know what's running and
# what each log last said — without copy-pasting out of DevTools. Read-only; starts
# nothing, kills nothing.
#
# Usage:  scripts/clarion-status.sh [tail_lines]   (default 12)
set -uo pipefail

N="${1:-12}"
WORKER_LOG="/tmp/clarion-worker.log"
BROKER_LOG="/tmp/clarion-broker.log"
EXT_LOG="/tmp/clarion-ext.log"

hr() { printf '%s\n' "------------------------------------------------------------------------"; }

port() {  # port label
  if lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1; then
    printf "  ✓ %-5s LISTENING  %s\n" "$1" "$2"
  else
    printf "  ✗ %-5s (down)     %s\n" "$1" "$2"
  fi
}

proc() {  # pattern label
  local pids; pids="$(pgrep -f "$1" 2>/dev/null | tr '\n' ' ')"
  if [ -n "$pids" ]; then printf "  ✓ %-26s %s\n" "$2" "$pids"; else printf "  ✗ %-26s (none)\n" "$2"; fi
}

echo "== CLARION STATUS =="
echo "branch: $(git -C /Users/tk/Desktop/conv-agent rev-parse --abbrev-ref HEAD 2>/dev/null)"
hr
echo "PORTS"
port 8770 "demo site (Next.js)"
port 8771 "relay broker ← extension (FROZEN wire)"
port 8772 "log sink (browser → /tmp/clarion-ext.log)"
port 8773 "relay broker ← agent (worker client)"
hr
echo "PROCESSES"
proc "clarion.app.voice_entry"      "voice worker"
proc "from multiprocessing.spawn"   "  └ LiveKit job subprocs"
proc "clarion.actuator.relay_broker" "relay broker"
proc "clarion-logsink.py"           "log sink"
proc "clarion-sim.py"               "real-sim (test driver)"
proc "user-data-dir=${CLARION_CHROME_PROFILE:-$HOME/.clarion/chromium-profile-durable}" "Chrome (clarion profile)"
hr
for f in "$WORKER_LOG" "$BROKER_LOG" "$EXT_LOG"; do
  echo "LAST $N — $f"
  if [ -f "$f" ]; then tail -n "$N" "$f" | sed 's/^/  /'; else echo "  (missing)"; fi
  hr
done
echo "tip: tail -f $WORKER_LOG $BROKER_LOG $EXT_LOG   ·   full status: docs/clarion-status.md"
