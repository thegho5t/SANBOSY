#!/usr/bin/env bash
# Launch the sandbox hardened for sharing with a few known people over a tunnel.
# Auth is REQUIRED (keys created below), with rate limits + abuse quarantine on.
# Run from a login shell so systemd --user is available (cgroup scopes).
set -euo pipefail
cd "$(dirname "$0")/.."

# --- create API keys once (their presence turns auth ON) ---
if [ ! -f "$HOME/.sandbox/api_keys.json" ]; then
  echo "== creating API keys (share these with your people; shown once) =="
  python3 scripts/make_key.py alice
  python3 scripts/make_key.py bob
  python3 scripts/make_key.py carol
  echo "== re-list any time: python3 scripts/make_key.py --list =="
fi

# --- hardened public-ish settings ---
export SANDBOX_WORKERS="${SANDBOX_WORKERS:-2}"          # bound total concurrency
export SANDBOX_QUEUE_DEPTH="${SANDBOX_QUEUE_DEPTH:-16}"
export SANDBOX_RATE_PER_MIN="${SANDBOX_RATE_PER_MIN:-20}"   # per identity
export SANDBOX_MAX_INFLIGHT="${SANDBOX_MAX_INFLIGHT:-2}"    # per identity
export SANDBOX_ABUSE_THRESHOLD="${SANDBOX_ABUSE_THRESHOLD:-15}"  # quarantine repeat abusers

echo "== serving on 127.0.0.1:8000 (auth required, rate-limited) =="
exec python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000
