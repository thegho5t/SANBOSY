#!/usr/bin/env bash
# M0 setup: verify environment, install runsc, build the shared read-only
# rootfs skeleton + minimal /etc on the ext4 side (~/.sandbox).
set -euo pipefail

RUNTIME_ROOT="${SANDBOX_RUNTIME_ROOT:-$HOME/.sandbox}"

echo "== checks =="
grep -qi microsoft /proc/version && echo "WSL2 kernel: $(uname -r)"
systemctl is-system-running >/dev/null 2>&1 || {
  echo "ERROR: systemd not running. Enable it in /etc/wsl.conf ([boot] systemd=true) and run 'wsl --shutdown'."; exit 1; }
[ -f /sys/fs/cgroup/cgroup.controllers ] || { echo "ERROR: cgroup v2 not mounted"; exit 1; }

echo "== runsc =="
if ! command -v runsc >/dev/null && [ ! -x "$HOME/.local/bin/runsc" ]; then
  ARCH=x86_64
  URL="https://storage.googleapis.com/gvisor/releases/release/latest/${ARCH}"
  tmp=$(mktemp -d)
  wget -q -P "$tmp" "$URL/runsc" "$URL/runsc.sha512"
  (cd "$tmp" && sha512sum -c runsc.sha512)
  chmod +x "$tmp/runsc"
  mkdir -p "$HOME/.local/bin"
  mv "$tmp/runsc" "$HOME/.local/bin/runsc"
fi
RUNSC="$(command -v runsc || echo "$HOME/.local/bin/runsc")"
"$RUNSC" --version

echo "== rootfs skeleton at $RUNTIME_ROOT =="
mkdir -p "$RUNTIME_ROOT"/{rootfs,etc,runs}
cd "$RUNTIME_ROOT/rootfs"
mkdir -p usr proc dev tmp box etc
for l in bin sbin lib lib64; do
  [ -e "$l" ] || ln -s "usr/$l" "$l"
done

cat > "$RUNTIME_ROOT/etc/passwd" <<'EOF'
root:x:0:0:root:/root:/usr/sbin/nologin
nobody:x:65534:65534:nobody:/box:/usr/sbin/nologin
EOF
cat > "$RUNTIME_ROOT/etc/group" <<'EOF'
root:x:0:
nogroup:x:65534:
EOF
# hostname/hosts kept minimal; there is no network anyway
echo sandbox > "$RUNTIME_ROOT/etc/hostname"
echo "127.0.0.1 localhost sandbox" > "$RUNTIME_ROOT/etc/hosts"

echo "== toolchains -> rootfs =="
# Copy each language's self-contained closure into the shared RO rootfs. Rootless
# gVisor on WSL2 can't bind-mount locked host dirs, so we copy once here.
BR="python3 $(dirname "$0")/build_rootfs.py"
command -v python3.12 >/dev/null && \
  $BR --bin /usr/bin/python3.12 --tree /usr/lib/python3.12 --link /usr/bin/python3:python3.12
command -v node >/dev/null && \
  $BR --bin /usr/bin/node --tree /usr/share/nodejs
if command -v gcc >/dev/null; then
  $BR --bin /usr/bin/x86_64-linux-gnu-gcc-13 --bin /usr/bin/x86_64-linux-gnu-g++-13 \
      --bin /usr/bin/x86_64-linux-gnu-as --bin /usr/bin/x86_64-linux-gnu-ld.bfd \
      --tree /usr/lib/gcc --tree /usr/libexec/gcc \
      --tree /usr/include --tree /usr/lib/x86_64-linux-gnu \
      --link /usr/bin/gcc:x86_64-linux-gnu-gcc-13 --link /usr/bin/g++:x86_64-linux-gnu-g++-13 \
      --link /usr/bin/as:x86_64-linux-gnu-as --link /usr/bin/ld:x86_64-linux-gnu-ld.bfd \
      --link /usr/bin/ld.bfd:x86_64-linux-gnu-ld.bfd
fi
# Ruby (interpreted)
command -v ruby >/dev/null && \
  $BR --bin /usr/bin/ruby3.2 --tree /usr/lib/ruby \
      --tree /usr/lib/x86_64-linux-gnu/ruby --link /usr/bin/ruby:ruby3.2
# Rust (compiled; cc linker provided by gcc above)
command -v rustc >/dev/null && \
  $BR --bin /usr/bin/rustc --tree /usr/lib/rustlib --link /usr/bin/cc:gcc
# Go (compiled; GOROOT bin + tools under /usr/lib, stdlib src under /usr/share)
if command -v go >/dev/null; then
  $BR --bin /usr/bin/go --tree /usr/lib/go-1.22 --tree /usr/share/go-1.22
  # Pre-warm the persistent build cache so the first sandboxed Go build is fast
  # (same Go version + content-addressed cache => sandbox reuses these entries).
  echo "== pre-warming Go build cache (one-time) =="
  mkdir -p "$RUNTIME_ROOT/cache/go"
  GOCACHE="$RUNTIME_ROOT/cache/go" go build std 2>/dev/null || true
fi

# Interactive shell + core utilities for the web terminal (bash + coreutils).
# Only userland tools — no new privileges; everything still runs inside gVisor.
echo "== shell + debug tools -> rootfs =="
SHELL_CANDIDATES=(
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
SHELL_BINS=()
for c in "${SHELL_CANDIDATES[@]}"; do [ -x "$c" ] && SHELL_BINS+=(--bin "$c"); done
SHELL_TREES=()
[ -d /usr/share/terminfo/x ] && SHELL_TREES+=(--tree /usr/share/terminfo/x)
[ -d /lib/terminfo ] && SHELL_TREES+=(--tree /lib/terminfo)
$BR "${SHELL_BINS[@]}" "${SHELL_TREES[@]}" --link /usr/bin/sh:bash

echo "== smoke test =="
"$RUNSC" --rootless --network=none --platform=systrap do /usr/bin/python3 -c 'print("sandbox-python-ok")'

echo "setup complete"
