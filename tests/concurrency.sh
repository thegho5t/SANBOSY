#!/usr/bin/env bash
# Fire N parallel fork bombs at the API; host must stay responsive and clean.
set -u
API=localhost:8000/api/v1
PAYLOAD='{"language":"python","files":[{"name":"main","content":"import os\nwhile True:\n try: os.fork()\n except OSError: print(\"capped\"); break"}],"run_timeout_ms":4000}'

echo "=== firing 6 parallel fork bombs ==="
for i in $(seq 1 6); do
  curl -s -X POST "$API/execute" -H "Content-Type: application/json" -d "$PAYLOAD" \
    -o "/tmp/fb_$i.json" &
done
wait
grep -o '"timed_out":[a-z]*\|"exit_code":[0-9a-z]*\|capped' /tmp/fb_*.json | sort | uniq -c

echo "=== host still responsive ==="
curl -s "$API/healthz"; echo
sleep 2
echo "=== leftover run dirs (want: only . and ..) ==="
ls -a "$HOME/.sandbox/runs/"
echo "=== stray runsc procs (want: 0) ==="
pgrep runsc | wc -l
