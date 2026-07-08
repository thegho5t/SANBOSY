#!/usr/bin/env bash
# Verify SSE streaming: a program that prints with delays should produce stdout
# events spread over time, then a final done event.
set -u
API=http://127.0.0.1:8000/api/v1
python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000 >/tmp/stream.log 2>&1 &
PID=$!
trap 'kill $PID 2>/dev/null' EXIT
for i in $(seq 1 30); do curl -sf "$API/healthz" >/dev/null 2>&1 && break; sleep 0.3; done

PROG='import time,sys\nfor i in range(3):\n print(f"chunk {i}", flush=True)\n time.sleep(0.4)'
BODY=$(python3 -c "import json;print(json.dumps({'language':'python','files':[{'name':'main.py','content':'import time,sys\nfor i in range(3):\n print(f\"chunk {i}\", flush=True)\n time.sleep(0.4)'}],'run_timeout_ms':8000}))")

echo "=== raw SSE stream (timestamps show live delivery) ==="
curl -sN -X POST "$API/execute/stream" -H "Content-Type: application/json" -d "$BODY" \
  | while IFS= read -r line; do
      [ -n "$line" ] && printf "%s  %s\n" "$(date +%S.%N | cut -c1-5)" "$line"
    done
