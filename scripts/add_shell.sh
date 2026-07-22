#!/usr/bin/env bash
# Add an interactive shell + core debugging utilities to the sandbox rootfs so
# the web terminal has something to run. This only populates *userland* tools —
# it grants no new privileges. Everything still runs inside gVisor, rootless,
# --network=none, non-root, under the same cgroup caps as the Run button.
#
# Idempotent: re-running just tops up anything missing.
set -euo pipefail
cd "$(dirname "$0")/.."
BR="python3 scripts/build_rootfs.py"

# Shell + a practical debug toolset. Only what's actually installed is included
# (build_rootfs would choke on a missing path), so this stays portable.
CANDIDATES=(
  /usr/bin/bash /usr/bin/dash
  /usr/bin/ls /usr/bin/cat /usr/bin/cp /usr/bin/mv /usr/bin/rm /usr/bin/mkdir
  /usr/bin/rmdir /usr/bin/touch /usr/bin/ln /usr/bin/chmod /usr/bin/pwd
  /usr/bin/head /usr/bin/tail /usr/bin/wc /usr/bin/sort /usr/bin/uniq
  /usr/bin/cut /usr/bin/tr /usr/bin/tee /usr/bin/nl /usr/bin/tac
  /usr/bin/env /usr/bin/printenv /usr/bin/id /usr/bin/date /usr/bin/sleep
  /usr/bin/dirname /usr/bin/basename /usr/bin/realpath /usr/bin/readlink
  /usr/bin/du /usr/bin/stat /usr/bin/seq /usr/bin/find /usr/bin/grep
  /usr/bin/sed /usr/bin/awk /usr/bin/mawk /usr/bin/diff /usr/bin/less
  /usr/bin/clear /usr/bin/vi /usr/bin/vim.tiny /usr/bin/nano /usr/bin/ps
  /usr/bin/which /usr/bin/xxd /usr/bin/hexdump /usr/bin/file /usr/bin/uname
  /usr/bin/nproc /usr/bin/timeout /usr/bin/md5sum /usr/bin/mktemp /usr/bin/watch
)
BINS=()
for c in "${CANDIDATES[@]}"; do
  [ -x "$c" ] && BINS+=(--bin "$c")
done

# xterm terminfo so bash line-editing / clear / less render correctly (small).
TREES=()
[ -d /usr/share/terminfo/x ] && TREES+=(--tree /usr/share/terminfo/x)
[ -d /lib/terminfo ] && TREES+=(--tree /lib/terminfo)

echo "== adding shell + ${#BINS[@]} tool flags to the rootfs =="
$BR "${BINS[@]}" "${TREES[@]}" --link /usr/bin/sh:bash

echo "== smoke test: run bash inside a real gVisor sandbox =="
python3 - <<'PY'
import asyncio
from app.executor.runner import execute, ExecutionRequest
r = asyncio.run(execute(ExecutionRequest(
    args=["/usr/bin/bash", "-lc",
          'echo shell-ok; echo "id: $(id -u):$(id -g)"; '
          'echo "tools: $(ls /usr/bin | wc -l)"; ls / | tr "\n" " "; echo']))
print("exit:", r.exit_code)
print("stdout:", r.stdout)
if r.stderr:
    print("stderr:", r.stderr[:400])
PY
