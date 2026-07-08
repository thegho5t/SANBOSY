#!/usr/bin/env bash
# Edge-case validation: malformed/abusive requests must be rejected cleanly
# (422 validation / 413 too-large / 400 bad-language), and valid ones still work.
set -u
API=http://127.0.0.1:8000/api/v1
python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000 >/tmp/edge.log 2>&1 &
PID=$!
trap 'kill $PID 2>/dev/null' EXIT
for i in $(seq 1 30); do curl -sf "$API/healthz" >/dev/null 2>&1 && break; sleep 0.3; done

code() { curl -s -o /dev/null -w "%{http_code}" -X POST "$API/execute" \
  -H "Content-Type: application/json" -d "$1"; }

py() { echo "{\"language\":\"python\",\"files\":[{\"name\":\"main.py\",\"content\":\"$1\"}]}"; }

echo "valid single file            -> $(code "$(py 'print(1)')")  (want 200)"
echo "empty files list             -> $(code '{"language":"python","files":[]}')  (want 422)"
echo "duplicate file names         -> $(code '{"language":"python","files":[{"name":"a.py","content":"x"},{"name":"a.py","content":"y"}]}')  (want 422)"
echo "path separator in name       -> $(code '{"language":"python","files":[{"name":"../evil.py","content":"x"}]}')  (want 422)"
echo "traversal name '..'          -> $(code '{"language":"python","files":[{"name":"..","content":"x"}]}')  (want 422)"
echo "empty file name              -> $(code '{"language":"python","files":[{"name":"","content":"x"}]}')  (want 422)"
echo "unknown language             -> $(code '{"language":"cobol","files":[{"name":"a","content":"x"}]}')  (want 400)"

# >16 files
FILES=$(python3 -c 'import json;print(json.dumps([{"name":f"f{i}.py","content":"x"} for i in range(20)]))')
echo "too many files (20)          -> $(code "{\"language\":\"python\",\"files\":$FILES}")  (want 422)"

# unicode content still runs
echo "unicode content             -> $(code '{"language":"python","files":[{"name":"main.py","content":"print(\"café ❤\")"}]}')  (want 200)"

# oversized source/stdin are too big for command-line args; see edge_big.sh
