#!/usr/bin/env bash
# Verify abuse detection: hostile runs get flagged, score accrues, and once the
# threshold is crossed the identity is quarantined (403). Threshold 5:
# one network_probe (3) + one timeout (2) = 5 -> quarantined.
set -u
API=http://127.0.0.1:8000/api/v1

SANDBOX_ABUSE_THRESHOLD=5 \
  python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000 \
  >/tmp/abuse-uvicorn.log 2>&1 &
PID=$!
trap 'kill $PID 2>/dev/null' EXIT
for i in $(seq 1 30); do curl -sf "$API/healthz" >/dev/null 2>&1 && break; sleep 0.3; done

post() { curl -s -o /dev/null -w "%{http_code}" -X POST "$API/execute" \
  -H "Content-Type: application/json" -d "$1"; }

NET='{"language":"python","files":[{"name":"main.py","content":"import socket;socket.create_connection((\"1.1.1.1\",80),2)"}]}'
LOOP='{"language":"python","files":[{"name":"main.py","content":"while True: pass"}],"run_timeout_ms":1000}'
OK='{"language":"python","files":[{"name":"main.py","content":"print(1)"}]}'

echo "=== network-probe run -> $(post "$NET") (flagged network_probe, weight 3) ==="
echo "=== timeout run       -> $(post "$LOOP") (flagged timeout, weight 2; score now 5) ==="

echo "=== abuse report ==="
curl -s "$API/abuse" | python3 -m json.tool

echo "=== next run is now quarantined -> expect 403 ==="
echo "status=$(post "$OK")"

echo "=== history marks suspicious runs ==="
curl -s "$API/runs?limit=5" | python3 -c 'import sys,json;[print(r["language"],"suspicious=",r["suspicious"],r["flags"]) for r in json.load(sys.stdin)["runs"]]'
