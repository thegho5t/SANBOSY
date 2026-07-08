#!/usr/bin/env bash
# Malformed-input fuzzing: the API must answer every bad request with a clean 4xx
# (never 500 / never crash), and path-traversal / control-char file names are
# rejected at the validation layer (defense-in-depth with the runner's sanitizer).
set -u
API=http://127.0.0.1:8000/api/v1
python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000 >/tmp/mal.log 2>&1 &
PID=$!
trap 'kill $PID 2>/dev/null; rm -rf "$tmp"' EXIT
for i in $(seq 1 30); do curl -sf "$API/healthz" >/dev/null 2>&1 && break; sleep 0.3; done

n_ok=0; n_bad=0
tmp=$(mktemp -d)

try() {  # inline body
  local want="$1" desc="$2" body="$3" st
  st=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$API/execute" \
        -H 'Content-Type: application/json' --data-binary "$body")
  if [[ "$st" =~ $want ]]; then printf "  [ok]  %-30s -> %s\n" "$desc" "$st"; n_ok=$((n_ok+1))
  else printf "  [BAD] %-30s -> %s (wanted %s)\n" "$desc" "$st" "$want"; n_bad=$((n_bad+1)); fi
}
tryf() {  # body from a file (large payloads: avoids argv length limits)
  local want="$1" desc="$2" file="$3" st
  st=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$API/execute" \
        -H 'Content-Type: application/json' --data-binary "@$file")
  if [[ "$st" =~ $want ]]; then printf "  [ok]  %-30s -> %s\n" "$desc" "$st"; n_ok=$((n_ok+1))
  else printf "  [BAD] %-30s -> %s (wanted %s)\n" "$desc" "$st" "$want"; n_bad=$((n_bad+1)); fi
}

python3 -c "import json;print(json.dumps({'language':'python','files':[{'name':'main.py','content':'x'*300000}]}))" > "$tmp/big.json"
python3 -c "import json;print(json.dumps({'language':'python','files':[{'name':'main.py','content':'x'}],'stdin':'s'*300000}))" > "$tmp/bigstdin.json"
python3 -c "import json;print(json.dumps({'language':'python','files':[{'name':f'f{i}.py','content':'x'} for i in range(50)]}))" > "$tmp/many.json"
python3 -c "import json;print(json.dumps({'language':'python','files':[{'name':'a\nb','content':'x'}]}))" > "$tmp/ctrl.json"
python3 -c "import json;print(json.dumps({'language':'python','files':[{'name':'../../etc/passwd','content':'x'}]}))" > "$tmp/trav.json"

echo "=== malformed / oversized inputs must be 4xx (never 5xx) ==="
try  '^4'   "unknown language"        '{"language":"cobol","files":[{"name":"main.py","content":"x"}]}'
try  '^4'   "empty files array"       '{"language":"python","files":[]}'
try  '^422' "missing files field"     '{"language":"python"}'
try  '^422' "missing language"        '{"files":[{"name":"a","content":"x"}]}'
try  '^422' "wrong type for files"    '{"language":"python","files":"notalist"}'
tryf '^41'  "oversized source >256KB" "$tmp/big.json"
tryf '^422' "oversized stdin >256KB"  "$tmp/bigstdin.json"
tryf '^422' "too many files >16"      "$tmp/many.json"
try  '^422' "empty filename"          '{"language":"python","files":[{"name":"","content":"x"}]}'
try  '^422' "path-separator filename" '{"language":"python","files":[{"name":"a/b","content":"x"}]}'
tryf '^422' "control-char filename"   "$tmp/ctrl.json"
tryf '^422' "path-traversal filename" "$tmp/trav.json"
try  '^422' "run_timeout too large"   '{"language":"python","files":[{"name":"main.py","content":"x"}],"run_timeout_ms":999999}'
try  '^422' "run_timeout negative"    '{"language":"python","files":[{"name":"main.py","content":"x"}],"run_timeout_ms":-5}'

echo "=== malformed JSON body ==="
st=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$API/execute" -H 'Content-Type: application/json' --data-binary '{not valid json')
if [[ "$st" =~ ^422 ]]; then echo "  [ok]  broken JSON -> $st"; n_ok=$((n_ok+1)); else echo "  [BAD] broken JSON -> $st"; n_bad=$((n_bad+1)); fi

echo "=== host + server intact after the barrage ==="
test -f /etc/passwd && echo "  host /etc/passwd intact: yes"
curl -s "$API/healthz"; echo
echo "  500s/tracebacks in server log (must be 0): $(grep -c '500 Internal Server Error\|Traceback' /tmp/mal.log)"

echo "RESULT: ok=$n_ok bad=$n_bad"
