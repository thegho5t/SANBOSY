#!/usr/bin/env bash
set -u
API=http://127.0.0.1:8005/api/v1
python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8005 >/tmp/final.log 2>&1 &
P=$!
trap 'kill $P 2>/dev/null' EXIT
for i in $(seq 1 30); do curl -sf "$API/healthz" >/dev/null 2>&1 && break; sleep 0.3; done
for ep in healthz languages stats abuse runs; do
  printf "%-10s -> %s\n" "$ep" "$(curl -s -o /dev/null -w '%{http_code}' "$API/$ep")"
done
echo -n "execute   -> "
curl -s -o /dev/null -w '%{http_code}\n' -X POST "$API/execute" \
  -H "Content-Type: application/json" \
  -d '{"language":"python","files":[{"name":"main.py","content":"print(1)"}]}'
