#!/usr/bin/env bash
# Print the current public tunnel URL (it changes whenever the tunnel restarts).
URL=$(journalctl --user -u sandbox-tunnel.service -n 200 --no-pager 2>/dev/null \
       | grep -oE "https://[a-z0-9-]+\.trycloudflare\.com" | tail -1)
if [ -n "$URL" ]; then
  echo "$URL"
else
  echo "no URL yet — is the tunnel running?  systemctl --user status sandbox-tunnel"
fi
