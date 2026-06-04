#!/usr/bin/env bash
# clarion-watch.sh — long-running HEALTH RECORDER for the Clarion live stack.
#
# Snapshots the relay/port/session/worker state every INTERVAL seconds to a
# rolling status log, so a developer (or an agent driving the demo) can SEE the
# current status at a glance without probing by hand. This is the "keeps
# recording progress and current status" process — it never drives anything.
#
# Watch live:  tail -f /tmp/clarion-status.log
# Stop:        kill "$(cat /tmp/clarion-watch.pid)"
set -uo pipefail

INTERVAL="${CLARION_WATCH_INTERVAL:-10}"
STATUS="/tmp/clarion-status.log"
RELAY_LOG="/tmp/clarion-relay.log"
WORKER_LOG="/tmp/clarion-worker.log"
echo $$ > /tmp/clarion-watch.pid

echo "== clarion-watch every ${INTERVAL}s → $STATUS (Ctrl-C / kill \$(cat /tmp/clarion-watch.pid) to stop) =="
trap 'echo "[$(date "+%H:%M:%S")] watch stopped" | tee -a "$STATUS"; exit 0' INT TERM

while true; do
  TS="$(date '+%H:%M:%S')"

  # Relay WebSocket server on :8771 (the thing the extension connects to).
  RELAY_PID="$(lsof -ti tcp:8771 -sTCP:LISTEN 2>/dev/null | head -1)"
  [ -n "$RELAY_PID" ] && PORT="UP(pid $RELAY_PID)" || PORT="DOWN"

  # Voice worker (LiveKit agent), if running.
  pgrep -f "clarion.app.voice_entry" >/dev/null 2>&1 && WORKER="up" || WORKER="down"

  # Most recent extension session lifecycle + perception activity, from the log.
  SESS="$(grep -aE 'session\.(start|end)' "$RELAY_LOG" 2>/dev/null | tail -1 \
          | sed -E 's/.*(session\.[a-z]+[^|]*)/\1/' | cut -c1-46)"
  PERC="$(grep -a 'perceived' "$RELAY_LOG" 2>/dev/null | tail -1 \
          | sed -E 's/.*(perceived [0-9]+ [a-z ]*nodes).*/\1/' | cut -c1-30)"

  echo "[$TS] relay:8771=$PORT  worker=$WORKER  | ${SESS:-no-session}  | ${PERC:-no-perceive}" \
    | tee -a "$STATUS"
  sleep "$INTERVAL"
done
