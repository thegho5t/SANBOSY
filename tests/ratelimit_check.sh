#!/usr/bin/env bash
# Verify per-identity rate limiting. Runs with a tight cap (5 requests/min) so a
# burst of 10 fast jobs trips the limiter.
set -u
API=http://127.0.0.1:8000/api/v1

SANDBOX_RATE_PER_MIN=5 SANDBOX_MAX_INFLIGHT=3 \
  python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000 \
  >/tmp/rl-uvicorn.log 2>&1 &
PID=$!
trap 'kill $PID 2>/dev/null' EXIT
for i in $(seq 1 30); do curl -sf "$API/healthz" >/dev/null 2>&1 && break; sleep 0.3; done

echo "=== /stats shows limiter config ==="
curl -s "$API/stats" | python3 -c 'import sys,json;d=json.load(sys.stdin);print(d["rate_limit"])'

echo "=== 10 requests at a 5/min cap (sequential) ==="
FAST='{"language":"python","files":[{"name":"main.py","content":"print(1)"}]}'
for i in $(seq 1 10); do
  curl -s -o /dev/null -w "%{http_code} " -X POST "$API/execute" \
    -H "Content-Type: application/json" -d "$FAST"
done
echo
echo "(expect first 5 -> 200, rest -> 429)"

echo "=== Retry-After header on a rejected request ==="
curl -s -D - -o /dev/null -X POST "$API/execute" \
  -H "Content-Type: application/json" -d "$FAST" | grep -i "retry-after\|HTTP/"
