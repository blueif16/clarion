#!/usr/bin/env bash
# clarion-down.sh — stop everything scripts/clarion-up.sh started.
# Kills the voice worker, the always-on relay broker (:8771/:8773), the log sink
# (:8772), and the dedicated-profile Chrome instance.
set -uo pipefail

WORKER_PID_FILE="/tmp/clarion-worker.pid"
BROKER_PID_FILE="/tmp/clarion-broker.pid"
PROFILE="/tmp/clarion-chrome-profile"

echo "== Clarion down =="

# Recursively kill a pid and ALL its descendants (children first).
reap_tree() {
  local p="$1" c
  for c in $(pgrep -P "$p" 2>/dev/null); do reap_tree "$c"; done
  kill -9 "$p" 2>/dev/null || true
}

# Voice worker + its ENTIRE job-subprocess tree. LiveKit runs each job in a
# multiprocessing subprocess whose argv is `python -c from multiprocessing.spawn`
# (it does NOT contain "voice_entry"), so a name-only kill LEAKS them — they
# linger as ppid=1 orphans, stay registered, and STEAL future dispatches (a real
# bug that made the sim land on a stale, mis-configured job). Reap the tree.
if [ -f "$WORKER_PID_FILE" ]; then
  PID="$(cat "$WORKER_PID_FILE" 2>/dev/null || true)"
  if [ -n "${PID:-}" ] && kill -0 "$PID" 2>/dev/null; then
    reap_tree "$PID"
    echo "  stopped worker PID $PID + its job-subprocess tree"
  fi
  rm -f "$WORKER_PID_FILE"
fi
# Any stray worker mains (and their trees) not tracked by the pid file.
for p in $(pgrep -f "clarion.app.voice_entry" 2>/dev/null || true); do reap_tree "$p"; done
# Orphaned LiveKit job subprocesses (ppid=1, multiprocessing-spawn) left by an
# earlier crash/kill — sweep them so they can't keep competing for dispatches.
ORPHANS="$(ps -axo pid=,ppid=,command= 2>/dev/null | awk '$2==1 && /from multiprocessing.spawn/ {print $1}')"
if [ -n "$ORPHANS" ]; then
  echo "  reaping orphaned LiveKit job subprocesses (PIDs $(echo $ORPHANS | tr '\n' ' '))"
  kill -9 $ORPHANS 2>/dev/null || true
fi

# Relay broker (by recorded PID, then anything still holding :8771 / :8773).
if [ -f "$BROKER_PID_FILE" ]; then
  BPID="$(cat "$BROKER_PID_FILE" 2>/dev/null || true)"
  if [ -n "${BPID:-}" ] && kill -0 "$BPID" 2>/dev/null; then
    kill "$BPID" 2>/dev/null || true
    echo "  stopped relay broker PID $BPID"
  fi
  rm -f "$BROKER_PID_FILE"
fi
STILL="$(lsof -t -i tcp:8771 -i tcp:8773 2>/dev/null || true)"
if [ -n "$STILL" ]; then
  echo "  freeing relay ports :8771/:8773 (PIDs $STILL)"; kill $STILL 2>/dev/null || true
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
