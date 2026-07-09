#!/usr/bin/env bash
# One command to (re)start the shareable demo: hardened server + Cloudflare tunnel.
# Idempotent — if either is already running it reuses it. Prints the public URL
# and the API keys to hand out. Run from a login shell (systemd --user needed).
#
#   bash scripts/share.sh          # start (or show status) and print URL + keys
#   bash scripts/share.sh stop     # stop both
set -uo pipefail
cd "$(dirname "$0")/.."

CF="$HOME/.local/bin/cloudflared"
PUB_LOG=/tmp/pub.log
CF_LOG=/tmp/cf.log

stop() {
  pkill -f "uvicorn app.main" 2>/dev/null && echo "stopped server" || true
  pkill -f "cloudflared tunnel" 2>/dev/null && echo "stopped tunnel" || true
}
if [ "${1:-}" = "stop" ]; then stop; exit 0; fi

# --- server ---
if pgrep -f "uvicorn app.main" >/dev/null; then
  echo "server: already running"
else
  echo "server: starting…"
  nohup bash scripts/serve_public.sh >"$PUB_LOG" 2>&1 &
  disown
  for i in $(seq 1 30); do
    curl -sf localhost:8000/api/v1/healthz >/dev/null 2>&1 && break
    sleep 0.5
  done
  curl -sf localhost:8000/api/v1/healthz >/dev/null 2>&1 \
    && echo "server: up" || { echo "server: FAILED — see $PUB_LOG"; tail -5 "$PUB_LOG"; exit 1; }
fi

# --- tunnel ---
if pgrep -f "cloudflared tunnel" >/dev/null; then
  echo "tunnel: already running"
else
  echo "tunnel: starting…"
  nohup "$CF" tunnel --url http://localhost:8000 >"$CF_LOG" 2>&1 &
  disown
  for i in $(seq 1 40); do
    grep -qoE "https://[a-z0-9-]+\.trycloudflare\.com" "$CF_LOG" 2>/dev/null && break
    sleep 0.5
  done
fi

URL=$(grep -oE "https://[a-z0-9-]+\.trycloudflare\.com" "$CF_LOG" 2>/dev/null | head -1)

echo
echo "========================================================"
if [ -n "$URL" ]; then
  echo "  SHARE THIS LINK:  $URL"
else
  echo "  tunnel URL not found yet — check $CF_LOG"
fi
echo "--------------------------------------------------------"
echo "  API keys (send one per person, with the link):"
python3 scripts/make_key.py --list 2>/dev/null | sed 's/^/    /'
echo
echo "  Keys are shown in full only when first created. If you"
echo "  need to see a key value again, revoke and re-create:"
echo "    python3 scripts/make_key.py --revoke NAME"
echo "    python3 scripts/make_key.py NAME"
echo "--------------------------------------------------------"
echo "  Stop everything:  bash scripts/share.sh stop"
echo "  Abuse report:     curl -s localhost:8000/api/v1/abuse -H 'X-API-Key: <key>'"
echo "========================================================"
