#!/usr/bin/env bash
# Turn this always-on machine into a durable host for the sandbox, exposed via a
# free Cloudflare tunnel. Runs the hardened server + tunnel as systemd --user
# services that auto-restart on crash and survive logout (linger). Unlike a
# terminal session, these come back on their own.  Run from a login shell:
#
#   bash deploy/home_install.sh
#
# Works on any systemd Linux (incl. WSL2 with systemd=true). For 24/7 on a Windows
# PC you must also stop it sleeping / keep WSL alive — see deploy/HOME.md.
set -euo pipefail
cd "$(dirname "$0")/.."
REPO="$(pwd)"
CF="$HOME/.local/bin/cloudflared"

command -v systemctl >/dev/null && systemctl --user show-environment >/dev/null 2>&1 \
  || { echo "systemd --user not available. In WSL: enable systemd in /etc/wsl.conf and run from a login shell."; exit 1; }

echo "== cloudflared =="
if [ ! -x "$CF" ]; then
  case "$(uname -m)" in x86_64) A=amd64;; aarch64) A=arm64;; *) A=amd64;; esac
  mkdir -p "$HOME/.local/bin"
  curl -fsSL "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-$A" -o "$CF"
  chmod +x "$CF"
fi
"$CF" --version | head -1

[ -d "$HOME/.sandbox/rootfs" ] || { echo "rootfs not built — run: bash scripts/setup_wsl2.sh"; exit 1; }

echo "== systemd --user services =="
mkdir -p "$HOME/.config/systemd/user"

cat > "$HOME/.config/systemd/user/sandbox-app.service" <<EOF
[Unit]
Description=Code Sandbox (hardened public server)
After=default.target
[Service]
Type=exec
WorkingDirectory=$REPO
ExecStart=/usr/bin/env bash $REPO/scripts/serve_public.sh
KillSignal=SIGTERM
TimeoutStopSec=40
Restart=always
RestartSec=3
[Install]
WantedBy=default.target
EOF

cat > "$HOME/.config/systemd/user/sandbox-tunnel.service" <<EOF
[Unit]
Description=Cloudflare tunnel for the Code Sandbox
After=sandbox-app.service
[Service]
Type=exec
ExecStart=$CF tunnel --no-autoupdate --protocol http2 --url http://localhost:8000
Restart=always
RestartSec=5
[Install]
WantedBy=default.target
EOF

# linger = services keep running without an open login session
if ! loginctl show-user "$USER" -p Linger 2>/dev/null | grep -q "Linger=yes"; then
  loginctl enable-linger "$USER" 2>/dev/null \
    || echo "NOTE: could not enable linger automatically. Run once:  sudo loginctl enable-linger $USER"
fi

systemctl --user daemon-reload
systemctl --user enable --now sandbox-app.service sandbox-tunnel.service
sleep 8

echo "== status =="
systemctl --user is-active sandbox-app.service && echo "app: active"
systemctl --user is-active sandbox-tunnel.service && echo "tunnel: active"

URL=$(journalctl --user -u sandbox-tunnel.service -n 80 --no-pager 2>/dev/null \
       | grep -oE "https://[a-z0-9-]+\.trycloudflare\.com" | tail -1)
echo
echo "=================================================================="
echo "  PUBLIC URL:  ${URL:-<not ready — run deploy/url.sh in a few seconds>}"
echo "  API keys (send one per person):"
python3 scripts/make_key.py --list | sed 's/^/    /'
echo "------------------------------------------------------------------"
echo "  Get URL anytime:   bash deploy/url.sh"
echo "  Logs:              journalctl --user -u sandbox-tunnel -f"
echo "  Stop hosting:      systemctl --user disable --now sandbox-app sandbox-tunnel"
echo "  Keep it up 24/7 on Windows: see deploy/HOME.md"
echo "=================================================================="
