#!/usr/bin/env bash
# Verify the job queue: normal execution, /stats, and backpressure (429).
# Runs with a tiny pool (2 workers, depth 2) so a burst overflows.
set -u
API=http://127.0.0.1:8000/api/v1

SANDBOX_WORKERS=2 SANDBOX_QUEUE_DEPTH=2 \
  python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000 \
  >/tmp/queue-uvicorn.log 2>&1 &
PID=$!
trap 'kill $PID 2>/dev/null' EXIT
for i in $(seq 1 30); do curl -sf "$API/healthz" >/dev/null 2>&1 && break; sleep 0.3; done

echo "=== /stats at rest ==="
curl -s "$API/stats"; echo

echo "=== normal run through the queue ==="
curl -s -X POST "$API/execute" -H "Content-Type: application/json" \
  -d '{"language":"python","files":[{"name":"main.py","content":"print(6*7)"}]}' \
  | python3 -c 'import sys,json;d=json.load(sys.stdin);print("stdout=",d["stdout"].strip(),"exit=",d["exit_code"])'

echo "=== backpressure: fire 12 slow jobs at a 2-worker/2-depth pool ==="
# Each job sleeps ~2s inside the sandbox; capacity is 2 running + 2 queued = 4,
# so the rest should get 429.
SLOW='{"language":"python","files":[{"name":"main.py","content":"import time; time.sleep(2); print(\"done\")"}],"run_timeout_ms":8000}'
codes=$(for i in $(seq 1 12); do
  curl -s -o /dev/null -w "%{http_code}\n" -X POST "$API/execute" \
    -H "Content-Type: application/json" -d "$SLOW" &
done; wait)
echo "$codes" | sort | uniq -c
echo "(expect a mix of 200 and 429)"
