#!/usr/bin/env bash
# Clarion — drop your MiniMax credentials into agent/.env (gitignored).
#
# MiniMax now powers BOTH the brain (MiniMax-M3, OpenAI-compatible) and the voice
# (Speech 2.6-turbo), wired through the LiveKit `minimax` plugin. This script
# upserts the MiniMax keys into agent/.env without echoing the secret and without
# disturbing your other keys (LiveKit, Deepgram, Moss, Google).
#
# Usage:
#   scripts/set-minimax-key.sh                 # interactive (prompts, key hidden)
#   scripts/set-minimax-key.sh <API_KEY>       # non-interactive key
#   scripts/set-minimax-key.sh <API_KEY> <GROUP_ID>
#
# It only writes the API key + group id (and sensible model/voice DEFAULTS if those
# lines are missing); it never overwrites a model/voice you've already customized.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$REPO_ROOT/agent/.env"
ENV_EXAMPLE="$REPO_ROOT/agent/.env.example"

# Replace any existing `KEY=...` line, else append. printf keeps the value verbatim.
upsert() {
  local key="$1" val="$2" file="$3" tmp
  tmp="$(mktemp)"
  grep -vE "^[[:space:]]*${key}=" "$file" > "$tmp" 2>/dev/null || true
  printf '%s=%s\n' "$key" "$val" >> "$tmp"
  mv "$tmp" "$file"
}

# Add `KEY=val` only if the file has no KEY= line yet (preserve user customizations).
upsert_if_missing() {
  local key="$1" val="$2" file="$3"
  grep -qE "^[[:space:]]*${key}=" "$file" 2>/dev/null || printf '%s=%s\n' "$key" "$val" >> "$file"
}

# Ensure agent/.env exists (seed from the template if it's the first run).
if [ ! -f "$ENV_FILE" ]; then
  if [ -f "$ENV_EXAMPLE" ]; then
    cp "$ENV_EXAMPLE" "$ENV_FILE"
    echo "Created $ENV_FILE from .env.example"
  else
    touch "$ENV_FILE"
    echo "Created empty $ENV_FILE"
  fi
fi

# --- collect the credentials -------------------------------------------------
API_KEY="${1:-}"
GROUP_ID="${2:-}"

if [ -z "$API_KEY" ]; then
  printf 'MiniMax API key (input hidden): '
  read -rs API_KEY
  printf '\n'
fi
if [ -z "$API_KEY" ]; then
  echo "ERROR: no API key provided — nothing written." >&2
  exit 1
fi

if [ -z "$GROUP_ID" ]; then
  printf 'MiniMax Group ID (optional — Enter to skip): '
  read -r GROUP_ID
fi

# --- write -------------------------------------------------------------------
upsert MINIMAX_API_KEY "$API_KEY" "$ENV_FILE"
if [ -n "$GROUP_ID" ]; then
  upsert MINIMAX_GROUP_ID "$GROUP_ID" "$ENV_FILE"
fi

# Defaults only if absent — don't clobber a voice/model you've tuned.
upsert_if_missing MINIMAX_LLM_MODEL "MiniMax-M3" "$ENV_FILE"
upsert_if_missing MINIMAX_TTS_MODEL "speech-2.6-turbo" "$ENV_FILE"
upsert_if_missing MINIMAX_TTS_VOICE "Friendly_Person" "$ENV_FILE"

chmod 600 "$ENV_FILE" 2>/dev/null || true

# --- confirm (masked) --------------------------------------------------------
mask() { local s="$1"; local n=${#s}; if [ "$n" -le 8 ]; then echo "****"; else echo "${s:0:4}…${s: -4} (${n} chars)"; fi; }

echo
echo "✓ Wrote MiniMax credentials to $ENV_FILE"
echo "    MINIMAX_API_KEY   = $(mask "$API_KEY")"
[ -n "$GROUP_ID" ] && echo "    MINIMAX_GROUP_ID  = $GROUP_ID" || echo "    MINIMAX_GROUP_ID  = (not set — the LiveKit minimax plugin & t2a_v2 may need it)"
echo "    MINIMAX_LLM_MODEL = $(grep -E '^MINIMAX_LLM_MODEL=' "$ENV_FILE" | head -1 | cut -d= -f2-)"
echo "    MINIMAX_TTS_MODEL = $(grep -E '^MINIMAX_TTS_MODEL=' "$ENV_FILE" | head -1 | cut -d= -f2-)"
echo "    MINIMAX_TTS_VOICE = $(grep -E '^MINIMAX_TTS_VOICE=' "$ENV_FILE" | head -1 | cut -d= -f2-)"
echo
echo "Next:"
echo "  cd agent && pip install -e \".[spike]\"      # pulls livekit-plugins-minimax + httpx"
echo "  .venv/bin/python -m clarion.app.gov_proof   # autonomous proof on the MiniMax stack"
