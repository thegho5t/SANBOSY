#!/usr/bin/env bash
# One-shot production setup for a fresh Ubuntu 24.04 VM (Oracle Always Free ARM or
# x86, or any root Linux box). Installs gVisor + toolchains, builds the rootfs,
# installs the app as a systemd --user service, and puts Caddy in front for
# automatic HTTPS. Run it as your normal sudo-capable login user (NOT root):
#
#   git clone https://github.com/thegho5t/SANBOSY.git ~/sandbox
#   cd ~/sandbox
#   DOMAIN=sandbosy.duckdns.org bash deploy/cloud_setup.sh
#
# DOMAIN is required for HTTPS (a free DuckDNS subdomain pointed at this VM's
# public IP works great). See deploy/DEPLOY.md for the full walkthrough.
set -euo pipefail

: "${DOMAIN:?Set DOMAIN=your.domain (e.g. sandbosy.duckdns.org). See deploy/DEPLOY.md}"
[ "$(id -u)" -ne 0 ] || { echo "Run as your normal user, not root (it uses sudo internally)."; exit 1; }
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO"

echo "== detecting platform =="
ARCH=$(uname -m)                                   # x86_64 | aarch64
echo "arch=$ARCH"
. /etc/os-release
[ "${VERSION_ID:-}" = "24.04" ] || echo "WARN: tuned for Ubuntu 24.04; on $VERSION_ID versions may differ."

echo "== packages =="
sudo apt-get update -y
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
  python3 python3-pip nodejs gcc g++ ruby rustc golang-go \
  caddy ufw curl git

echo "== python deps (app runs as this user) =="
python3 -m pip install --user --break-system-packages -e '.[redis]' 2>/dev/null \
  || python3 -m pip install --user --break-system-packages fastapi 'uvicorn[standard]' pydantic

echo "== install gVisor runsc ($ARCH) =="
if ! command -v runsc >/dev/null; then
  URL="https://storage.googleapis.com/gvisor/releases/release/latest/${ARCH}"
  tmp=$(mktemp -d)
  ( cd "$tmp" && curl -fsSLO "$URL/runsc" -O "$URL/runsc.sha512" && sha512sum -c runsc.sha512 )
  sudo install -m 0755 "$tmp/runsc" /usr/local/bin/runsc
  rm -rf "$tmp"
fi
runsc --version | head -1

echo "== build the shared read-only rootfs (arch-aware) =="
TRIPLE=$(gcc -dumpmachine)                          # x86_64-linux-gnu | aarch64-linux-gnu
GCCVER=$(gcc -dumpversion)                          # e.g. 13
GOVER=$(basename "$(ls -d /usr/lib/go-* | head -1)" | sed 's/go-//')  # e.g. 1.22
PYBIN=$(readlink -f "$(command -v python3)")        # /usr/bin/python3.12
PYVER=$(basename "$PYBIN" | sed 's/python//')       # 3.12
RBBIN=$(readlink -f "$(command -v ruby)")           # /usr/bin/ruby3.2
RUBYLIB=$(ruby -e 'print RbConfig::CONFIG["rubylibdir"]')     # /usr/lib/ruby/3.x
RUBYARCH=$(ruby -e 'print RbConfig::CONFIG["archdir"]')       # /usr/lib/<triple>/ruby/3.x
BR="python3 scripts/build_rootfs.py"

$BR --bin "$PYBIN" --tree "/usr/lib/python$PYVER" --link "/usr/bin/python3:python$PYVER"
command -v node >/dev/null && $BR --bin /usr/bin/node --tree /usr/share/nodejs
$BR --bin "$RBBIN" --tree "$RUBYLIB" --tree "$RUBYARCH" \
    --link "/usr/bin/ruby:$(basename "$RBBIN")"
$BR --bin "/usr/bin/${TRIPLE}-gcc-${GCCVER}" --bin "/usr/bin/${TRIPLE}-g++-${GCCVER}" \
    --bin "/usr/bin/${TRIPLE}-as" --bin "/usr/bin/${TRIPLE}-ld.bfd" \
    --tree /usr/lib/gcc --tree /usr/libexec/gcc --tree /usr/include --tree "/usr/lib/${TRIPLE}" \
    --link "/usr/bin/gcc:${TRIPLE}-gcc-${GCCVER}" --link "/usr/bin/g++:${TRIPLE}-g++-${GCCVER}" \
    --link "/usr/bin/as:${TRIPLE}-as" --link "/usr/bin/ld:${TRIPLE}-ld.bfd" \
    --link "/usr/bin/ld.bfd:${TRIPLE}-ld.bfd" --link "/usr/bin/cc:gcc"
command -v rustc >/dev/null && $BR --bin /usr/bin/rustc --tree /usr/lib/rustlib
if command -v go >/dev/null; then
  $BR --bin /usr/bin/go --tree "/usr/lib/go-${GOVER}" --tree "/usr/share/go-${GOVER}"
  echo "== pre-warming Go build cache =="
  mkdir -p "$HOME/.sandbox/cache/go"
  GOCACHE="$HOME/.sandbox/cache/go" go build std 2>/dev/null || true
fi
# Go's GOROOT is version-pinned in the registry; align if the VM's Go differs.
[ "$GOVER" = "1.22" ] || echo "NOTE: VM has Go $GOVER; edit GOROOT in app/languages/registry.py to /usr/lib/go-$GOVER"

echo "== production config (auth + rate limits + abuse on) =="
mkdir -p "$HOME/.config"
cat > "$HOME/.config/sandbox.env" <<EOF
SANDBOX_WORKERS=3
SANDBOX_QUEUE_DEPTH=24
SANDBOX_RATE_PER_MIN=20
SANDBOX_MAX_INFLIGHT=2
SANDBOX_ABUSE_THRESHOLD=15
SANDBOX_CACHE_PER_IDENTITY=1
EOF

echo "== API keys (share one per person; shown once) =="
if [ ! -f "$HOME/.sandbox/api_keys.json" ]; then
  for name in alice bob carol; do python3 scripts/make_key.py "$name"; done
fi

echo "== systemd --user service (survives logout via linger) =="
mkdir -p "$HOME/.config/systemd/user"
sed "s#%h/sandbox#$REPO#" deploy/sandbox.service > "$HOME/.config/systemd/user/sandbox.service"
loginctl enable-linger "$USER"
systemctl --user daemon-reload
systemctl --user enable --now sandbox
sleep 3
curl -sf http://127.0.0.1:8000/api/v1/healthz >/dev/null && echo "app: up on 127.0.0.1:8000" \
  || { echo "app FAILED — journalctl --user -u sandbox -n 40"; exit 1; }

echo "== Caddy reverse proxy + automatic HTTPS for $DOMAIN =="
sudo tee /etc/caddy/Caddyfile >/dev/null <<EOF
$DOMAIN {
    encode zstd gzip
    reverse_proxy 127.0.0.1:8000
}
EOF
sudo systemctl restart caddy

echo "== host firewall (Oracle: ALSO open 80/443 in the VCN Security List!) =="
sudo ufw allow 22/tcp >/dev/null; sudo ufw allow 80/tcp >/dev/null; sudo ufw allow 443/tcp >/dev/null
sudo ufw --force enable >/dev/null || true

echo
echo "=================================================================="
echo "  DEPLOYED.  Public URL:  https://$DOMAIN"
echo "  (give friends the URL + one API key each, listed above)"
echo "  If it doesn't load: open ports 80 & 443 in the Oracle VCN"
echo "  Security List / NSG — ufw alone is not enough on Oracle."
echo "  Logs:   journalctl --user -u sandbox -f"
echo "  Keys:   python3 scripts/make_key.py --list"
echo "=================================================================="
