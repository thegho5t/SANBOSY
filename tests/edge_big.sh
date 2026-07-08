#!/usr/bin/env bash
# Oversized payloads (too big for command-line args) via files.
set -u
API=http://127.0.0.1:8000/api/v1
python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000 >/tmp/eb.log 2>&1 &
PID=$!
trap 'kill $PID 2>/dev/null' EXIT
for i in $(seq 1 30); do curl -sf "$API/healthz" >/dev/null 2>&1 && break; sleep 0.3; done

python3 - <<'PY'
import json
open("/tmp/big_src.json", "w").write(json.dumps(
    {"language": "python", "files": [{"name": "main.py", "content": "a" * 300000}]}))
open("/tmp/big_stdin.json", "w").write(json.dumps(
    {"language": "python", "files": [{"name": "main.py", "content": "print(1)"}],
     "stdin": "b" * 300000}))
PY

echo "oversized source -> $(curl -s -o /dev/null -w '%{http_code}' -X POST "$API/execute" -H 'Content-Type: application/json' -d @/tmp/big_src.json)  (want 413)"
echo "oversized stdin  -> $(curl -s -o /dev/null -w '%{http_code}' -X POST "$API/execute" -H 'Content-Type: application/json' -d @/tmp/big_stdin.json)  (want 422)"
