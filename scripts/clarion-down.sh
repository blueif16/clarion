#!/usr/bin/env bash
# clarion-down.sh — stop everything scripts/clarion-up.sh started.
# Kills the voice worker (relay :8771) and the dedicated-profile Chrome instance.
set -uo pipefail

WORKER_PID_FILE="/tmp/clarion-worker.pid"
PROFILE="/tmp/clarion-chrome-profile"

echo "== Clarion down =="

# Worker (by recorded PID, then anything still holding :8771).
if [ -f "$WORKER_PID_FILE" ]; then
  PID="$(cat "$WORKER_PID_FILE" 2>/dev/null || true)"
  if [ -n "${PID:-}" ] && kill -0 "$PID" 2>/dev/null; then
    kill "$PID" 2>/dev/null || true
    echo "  stopped worker PID $PID"
  fi
  rm -f "$WORKER_PID_FILE"
fi
STILL="$(lsof -ti tcp:8771 2>/dev/null || true)"
if [ -n "$STILL" ]; then
  echo "  freeing :8771 (PIDs $STILL)"; kill $STILL 2>/dev/null || true
fi

# Browser-log sink on :8772.
SINK="$(lsof -ti tcp:8772 2>/dev/null || true)"
if [ -n "$SINK" ]; then
  echo "  stopping log sink (:8772, PIDs $SINK)"; kill $SINK 2>/dev/null || true
fi
rm -f /tmp/clarion-logsink.pid

# The dedicated-profile Chrome instance only (never the user's main Chrome).
PIDS="$(pgrep -f "user-data-dir=$PROFILE" 2>/dev/null || true)"
if [ -n "$PIDS" ]; then
  kill $PIDS 2>/dev/null || true
  echo "  closed the Clarion Chrome profile instance"
fi

echo "  done."
