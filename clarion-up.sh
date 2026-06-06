#!/usr/bin/env bash
# Root convenience wrapper. The REAL launcher lives in scripts/clarion-up.sh so its
# relative repo-root resolution (dirname/..) stays correct — do NOT move that file.
# Run from the repo root:  ./clarion-up.sh [START_URL]
exec "$(dirname "$0")/scripts/clarion-up.sh" "$@"
