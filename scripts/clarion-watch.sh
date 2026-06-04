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
BROKER_LOG="/tmp/clarion-broker.log"
WORKER_LOG="/tmp/clarion-worker.log"
echo $$ > /tmp/clarion-watch.pid

echo "== clarion-watch every ${INTERVAL}s → $STATUS (Ctrl-C / kill \$(cat /tmp/clarion-watch.pid) to stop) =="
trap 'echo "[$(date "+%H:%M:%S")] watch stopped" | tee -a "$STATUS"; exit 0' INT TERM

while true; do
  TS="$(date '+%H:%M:%S')"

  # Relay broker: extension side (:8771, what the extension connects to) and the
  # agent side (:8773, what the actuator connects to).
  EXT_PID="$(lsof -ti tcp:8771 -sTCP:LISTEN 2>/dev/null | head -1)"
  [ -n "$EXT_PID" ] && EXT="UP(pid $EXT_PID)" || EXT="DOWN"
  AGT_PID="$(lsof -ti tcp:8773 -sTCP:LISTEN 2>/dev/null | head -1)"
  [ -n "$AGT_PID" ] && AGT="UP" || AGT="DOWN"

  # Voice worker (LiveKit agent), if running.
  pgrep -f "clarion.app.voice_entry" >/dev/null 2>&1 && WORKER="up" || WORKER="down"

  # Most recent session lifecycle (broker log) + the latest worker loop phase.
  SESS="$(grep -aE 'session\.(start|end)' "$BROKER_LOG" 2>/dev/null | tail -1 \
          | sed -E 's/.*\[relay-broker\] //' | cut -c1-46)"
  LOOP="$(grep -a '\[loop\]' "$WORKER_LOG" 2>/dev/null | tail -1 \
          | sed -E 's/.*\[loop\] //' | cut -c1-40)"

  echo "[$TS] broker:8771=$EXT 8773=$AGT  worker=$WORKER  | ${SESS:-no-session}  | ${LOOP:-no-loop}" \
    | tee -a "$STATUS"
  sleep "$INTERVAL"
done
