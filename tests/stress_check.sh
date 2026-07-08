#!/usr/bin/env bash
# Concurrent-load stress: fire a heavy mix of hostile + benign jobs at once and
# verify (a) isolation holds — each benign job sees only its own /box and output,
# (b) the host and server survive, (c) no fd/process/run-dir leakage afterward.
set -u
API=http://127.0.0.1:8000/api/v1
python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000 >/tmp/stress.log 2>&1 &
PID=$!
trap 'kill $PID 2>/dev/null' EXIT
for i in $(seq 1 30); do curl -sf "$API/healthz" >/dev/null 2>&1 && break; sleep 0.3; done

fds_before=$(ls /proc/$PID/fd 2>/dev/null | wc -l)
echo "server fds before: $fds_before"

post() { curl -s -X POST "$API/execute" -H 'Content-Type: application/json' -d "$1"; }

FORK='{"language":"python","files":[{"name":"main.py","content":"import os\nwhile True:\n try:os.fork()\n except OSError:break"}],"run_timeout_ms":3000}'
LOOP='{"language":"python","files":[{"name":"main.py","content":"while True:pass"}],"run_timeout_ms":2000}'
MEM='{"language":"python","files":[{"name":"main.py","content":"x=[]\nwhile True:x.append(b\"A\"*10485760)"}],"run_timeout_ms":4000}'

# 10 benign jobs each print a unique token; interleaved with hostile ones.
tmp=$(mktemp -d)
pids=()
for i in $(seq 1 10); do
  ( post "{\"language\":\"python\",\"files\":[{\"name\":\"main.py\",\"content\":\"print('TOKEN_$i')\"}]}" \
      > "$tmp/benign_$i.json" ) & pids+=($!)
  ( post "$FORK" >/dev/null ) & pids+=($!)
  ( post "$LOOP" >/dev/null ) & pids+=($!)
  ( post "$MEM"  >/dev/null ) & pids+=($!)
done
for p in "${pids[@]}"; do wait "$p"; done

echo "=== isolation: each benign job saw only its own token ==="
leak=0
for i in $(seq 1 10); do
  out=$(python3 -c "import json;print(json.load(open('$tmp/benign_$i.json')).get('stdout','').strip())" 2>/dev/null)
  if [ "$out" != "TOKEN_$i" ]; then echo "  job $i: got '$out' (expected TOKEN_$i)"; leak=1; fi
done
[ $leak -eq 0 ] && echo "  all 10 benign outputs correct, no cross-run leakage"

echo "=== host + server survived ==="
curl -s "$API/healthz"; echo

sleep 2
fds_after=$(ls /proc/$PID/fd 2>/dev/null | wc -l)
echo "server fds after: $fds_after (before $fds_before; growth should be small)"
echo "leftover run dirs: $(ls "$HOME/.sandbox/runs/" 2>/dev/null | wc -l)"
echo "stray runsc procs: $(ps -eo comm | grep -c '^runsc$')"
rm -rf "$tmp"
