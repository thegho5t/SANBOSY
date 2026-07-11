#!/usr/bin/env bash
set -u
API=http://127.0.0.1:8000/api/v1
KEY="$1"
SANDBOX_RATE_PER_MIN=20 SANDBOX_MAX_INFLIGHT=2 SANDBOX_ABUSE_THRESHOLD=15 \
  python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000 >/tmp/pub.log 2>&1 &
P=$!
trap 'kill $P 2>/dev/null' EXIT
for i in $(seq 1 30); do curl -sf "$API/healthz" >/dev/null 2>&1 && break; sleep 0.3; done

PY='{"language":"python","files":[{"name":"main.py","content":"print(6*7)"}]}'
echo "auth_required: $(curl -s "$API/languages" | python3 -c 'import sys,json;print(json.load(sys.stdin)["auth_required"])')"
echo "no key   -> $(curl -s -o /dev/null -w '%{http_code}' -X POST "$API/execute" -H 'Content-Type: application/json' -d "$PY")  (want 401)"
echo "bad key  -> $(curl -s -o /dev/null -w '%{http_code}' -X POST "$API/execute" -H 'Content-Type: application/json' -H 'X-API-Key: sk_wrong' -d "$PY")  (want 401)"
echo "good key -> $(curl -s -X POST "$API/execute" -H 'Content-Type: application/json' -H "X-API-Key: $KEY" -d "$PY" | python3 -c 'import sys,json;d=json.load(sys.stdin);print("stdout",d["stdout"].strip(),"exit",d["exit_code"])')"
