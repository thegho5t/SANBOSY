#!/usr/bin/env bash
# End-to-end auth check. Assumes a key named 'operator' exists; pass the raw key
# as $1. Starts uvicorn from a login shell, exercises the three cases, stops it.
set -u
KEY="$1"
API=http://127.0.0.1:8000/api/v1

python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000 >/tmp/auth-uvicorn.log 2>&1 &
PID=$!
trap 'kill $PID 2>/dev/null' EXIT
for i in $(seq 1 30); do curl -sf "$API/healthz" >/dev/null 2>&1 && break; sleep 0.3; done

echo "=== /languages advertises auth_required ==="
curl -s "$API/languages" | python3 -c 'import sys,json;print("auth_required =", json.load(sys.stdin)["auth_required"])'

PAYLOAD='{"language":"python","files":[{"name":"main.py","content":"print(123)"}]}'

echo "=== no key -> expect 401 ==="
curl -s -o /dev/null -w "status=%{http_code}\n" -X POST "$API/execute" \
  -H "Content-Type: application/json" -d "$PAYLOAD"

echo "=== wrong key -> expect 401 ==="
curl -s -o /dev/null -w "status=%{http_code}\n" -X POST "$API/execute" \
  -H "Content-Type: application/json" -H "X-API-Key: sk_wrong" -d "$PAYLOAD"

echo "=== correct key -> expect 200 + output ==="
curl -s -w "\nstatus=%{http_code}\n" -X POST "$API/execute" \
  -H "Content-Type: application/json" -H "X-API-Key: $KEY" -d "$PAYLOAD"
