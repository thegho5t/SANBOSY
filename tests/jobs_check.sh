#!/usr/bin/env bash
# Verify async submit/poll: POST /jobs returns an id immediately, GET /jobs/{id}
# transitions to done with the result, and history still records the run.
set -u
API=http://127.0.0.1:8000/api/v1

python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000 \
  >/tmp/jobs-uvicorn.log 2>&1 &
PID=$!
trap 'kill $PID 2>/dev/null' EXIT
for i in $(seq 1 30); do curl -sf "$API/healthz" >/dev/null 2>&1 && break; sleep 0.3; done

SLOW='{"language":"python","files":[{"name":"main.py","content":"import time;time.sleep(1.5);print(\"async done\", 8*8)"}],"run_timeout_ms":8000}'

echo "=== POST /jobs (expect 202 + id, status queued) ==="
RESP=$(curl -s -w "\n%{http_code}" -X POST "$API/jobs" \
  -H "Content-Type: application/json" -d "$SLOW")
CODE=$(echo "$RESP" | tail -1)
JID=$(echo "$RESP" | head -1 | python3 -c 'import sys,json;print(json.load(sys.stdin)["id"])')
echo "http=$CODE job_id=$JID"

echo "=== poll GET /jobs/{id} until done ==="
for i in $(seq 1 20); do
  S=$(curl -s "$API/jobs/$JID")
  ST=$(echo "$S" | python3 -c 'import sys,json;print(json.load(sys.stdin)["status"])')
  echo "poll $i: status=$ST"
  if [ "$ST" = "done" ] || [ "$ST" = "error" ]; then
    echo "$S" | python3 -c 'import sys,json;d=json.load(sys.stdin);print("  result:",d["result"]["stdout"].strip(),"exit=",d["result"]["exit_code"],"history_id=",d["result"]["id"])'
    break
  fi
  sleep 0.5
done

echo "=== unknown job -> 404 ==="
curl -s -o /dev/null -w "status=%{http_code}\n" "$API/jobs/deadbeef"

echo "=== history recorded the async run ==="
curl -s "$API/runs?limit=1" | python3 -c 'import sys,json;r=json.load(sys.stdin)["runs"][0];print("language",r["language"],"exit",r["exit_code"])'

echo "=== sync /execute still works ==="
curl -s -X POST "$API/execute" -H "Content-Type: application/json" \
  -d '{"language":"python","files":[{"name":"main.py","content":"print(1)"}]}' \
  | python3 -c 'import sys,json;print("sync exit=",json.load(sys.stdin)["exit_code"])'
