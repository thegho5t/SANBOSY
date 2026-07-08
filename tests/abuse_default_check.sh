#!/usr/bin/env bash
# Default (no threshold): abuse is detected/flagged but NEVER blocks, and clean
# runs are not flagged.
set -u
API=http://127.0.0.1:8004/api/v1
python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8004 >/tmp/ad.log 2>&1 &
P=$!
trap 'kill $P 2>/dev/null' EXIT
for i in $(seq 1 30); do curl -sf "$API/healthz" >/dev/null 2>&1 && break; sleep 0.3; done

post() { curl -s -o /dev/null -w "%{http_code}" -X POST "$API/execute" \
  -H "Content-Type: application/json" -d "$1"; }
NET='{"language":"python","files":[{"name":"main.py","content":"import socket;socket.create_connection((\"1.1.1.1\",80),2)"}]}'
OK='{"language":"python","files":[{"name":"main.py","content":"print(1)"}]}'

echo "3 network probes at default threshold (0): $(post "$NET") $(post "$NET") $(post "$NET")"
echo "clean run still allowed: $(post "$OK")  (expect all 200 - never blocked)"
echo "report (threshold 0, quarantined must be false):"
curl -s "$API/abuse" | python3 -c 'import sys,json;d=json.load(sys.stdin);i=d["identities"].get("local",{});print("score",i.get("score"),"quarantined",i.get("quarantined"))'
echo "clean run flagged? (should be suspicious=False):"
curl -s "$API/runs?limit=1" | python3 -c 'import sys,json;r=json.load(sys.stdin)["runs"][0];print("language",r["language"],"suspicious",r["suspicious"],r["flags"])'
