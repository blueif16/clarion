#!/usr/bin/env bash
# clarion-down.sh — stop everything scripts/clarion-up.sh started.
# Kills the voice worker, the always-on relay broker (:8771/:8773), the log sink
# (:8772), and the dedicated-profile Chrome instance.
set -uo pipefail

WORKER_PID_FILE="/tmp/clarion-worker.pid"
BROKER_PID_FILE="/tmp/clarion-broker.pid"
# MUST match clarion-up.sh's profile. Was a stale /tmp path → `down` never matched
# the real Chrome instance, so it never closed → the extension never reloaded between
# runs (the "I restarted but still see old code / no toast" bug).
PROFILE="${CLARION_CHROME_PROFILE:-$HOME/.clarion/chromium-profile-durable}"

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
# bug that made the sim land on a stale, mis-configured job). We match the worker
# by argv via pgrep (NOT the pid file — nohup forks, so the recorded pid is
# off-by-one and could be recycled to an unrelated process) and reap its tree.
for p in $(pgrep -f "clarion.app.voice_entry" 2>/dev/null || true); do
  reap_tree "$p"; echo "  stopped voice worker tree (pid $p)"
done
rm -f "$WORKER_PID_FILE"
# Orphaned LiveKit job subprocesses (ppid=1, multiprocessing-spawn) left by an
# earlier crash/kill — sweep them so they can't keep competing for dispatches.
ORPHANS="$(ps -axo pid=,ppid=,command= 2>/dev/null | awk '$2==1 && /from multiprocessing.spawn/ {print $1}')"
if [ -n "$ORPHANS" ]; then
  echo "  reaping orphaned LiveKit job subprocesses (PIDs $(echo $ORPHANS | tr '\n' ' '))"
  kill -9 $ORPHANS 2>/dev/null || true
fi

# Relay broker (match by argv; then free the ports as a backstop).
for p in $(pgrep -f "clarion.actuator.relay_broker" 2>/dev/null || true); do
  kill "$p" 2>/dev/null && echo "  stopped relay broker (pid $p)" || true
done
rm -f "$BROKER_PID_FILE"
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
