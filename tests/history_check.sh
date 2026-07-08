#!/usr/bin/env bash
# Verify persistence/history: execute, list, retrieve, replay-source, delete.
set -u
API=http://127.0.0.1:8000/api/v1

python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000 \
  >/tmp/history-uvicorn.log 2>&1 &
PID=$!
trap 'kill $PID 2>/dev/null' EXIT
for i in $(seq 1 30); do curl -sf "$API/healthz" >/dev/null 2>&1 && break; sleep 0.3; done

echo "=== execute (returns history id) ==="
RID=$(curl -s -X POST "$API/execute" -H "Content-Type: application/json" \
  -d '{"language":"python","files":[{"name":"main.py","content":"print(\"remember me\")"}]}' \
  | python3 -c 'import sys,json;d=json.load(sys.stdin);print(d["id"])')
echo "run id = $RID"

# a second run so the list has more than one
curl -s -X POST "$API/execute" -H "Content-Type: application/json" \
  -d '{"language":"javascript","files":[{"name":"main.js","content":"console.log(1+2)"}]}' >/dev/null

echo "=== GET /runs (summaries) ==="
curl -s "$API/runs?limit=5" | python3 -c 'import sys,json;d=json.load(sys.stdin);[print(r["language"],r["exit_code"],r["id"][:8]) for r in d["runs"]]'

echo "=== GET /runs/{id} (full record incl. source) ==="
curl -s "$API/runs/$RID" | python3 -c 'import sys,json;d=json.load(sys.stdin);print("lang=",d["language"],"stdout=",d["stdout"].strip(),"source=",d["files"])'

echo "=== DELETE /runs/{id} -> 204 ==="
curl -s -o /dev/null -w "delete status=%{http_code}\n" -X DELETE "$API/runs/$RID"

echo "=== GET deleted -> 404 ==="
curl -s -o /dev/null -w "get-after-delete status=%{http_code}\n" "$API/runs/$RID"
