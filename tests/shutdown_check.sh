#!/usr/bin/env bash
# End-to-end graceful shutdown: with a ~3s job in flight, SIGTERM should let the
# server drain (take ~3s to exit), not kill the job instantly.
set -u
API=http://127.0.0.1:8000/api/v1
python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000 >/tmp/sd.log 2>&1 &
PID=$!
for i in $(seq 1 30); do curl -sf "$API/healthz" >/dev/null 2>&1 && break; sleep 0.3; done

# fire a 3s async job
curl -s -X POST "$API/jobs" -H 'Content-Type: application/json' \
  -d '{"language":"python","files":[{"name":"main.py","content":"import time;time.sleep(3);print(1)"}],"run_timeout_ms":8000}' >/dev/null
sleep 0.6   # let a worker pick it up

echo "sending SIGTERM with a 3s job in flight..."
START=$(date +%s.%N)
kill -TERM $PID
wait $PID 2>/dev/null
END=$(date +%s.%N)
DUR=$(python3 -c "print(f'{$END-$START:.1f}')")
echo "server exited after ${DUR}s (graceful drain ~= 3s; abrupt kill would be <1s)"
grep -iE "shutdown|drain|complete" /tmp/sd.log | tail -2
