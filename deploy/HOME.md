# Host it from a machine you own (free, durable)

Turns an always-on machine into a public host via a free Cloudflare tunnel. Unlike
running `share.sh` in a terminal, this uses **systemd services that auto-restart and
survive logout** — it won't keep dying.

## Install (once)
From a login shell in the repo:
```bash
bash deploy/home_install.sh
```
It installs `cloudflared`, creates two `systemd --user` services (`sandbox-app`,
`sandbox-tunnel`), enables linger, starts them, and prints your **public URL + API
keys**. Get the URL anytime with `bash deploy/url.sh`.

That's it on a **native Linux box or Raspberry Pi** — it survives crashes and reboots
automatically. Done.

## Keeping it up 24/7 on a Windows PC (WSL)
WSL only stays alive while Windows is awake and the distro is running, so add three
things:

1. **Never sleep:** Settings → System → Power → *Screen and sleep* → set **Sleep = Never**
   (at least when plugged in). Also disable "hibernate".
2. **Don't let WSL idle-shut-down:** create/edit `C:\Users\<you>\.wslconfig` with:
   ```
   [wsl2]
   vmIdleTimeout=-1
   ```
   then in PowerShell: `wsl --shutdown` once (it restarts on next use with the setting).
3. **Auto-start on boot/logon:** the linger'd services start when the WSL distro boots,
   but the distro only boots when something invokes WSL. Add a logon task so it boots
   itself — in PowerShell (once):
   ```powershell
   $act = New-ScheduledTaskAction -Execute "wsl.exe" -Argument "-d Ubuntu -u root -e true"
   $trg = New-ScheduledTaskTrigger -AtLogOn
   Register-ScheduledTask -TaskName "start-wsl-sandbox" -Action $act -Trigger $trg -RunLevel Highest
   ```
   (Booting the distro triggers the linger'd `sandbox-app` / `sandbox-tunnel` services.)

Honest note: a Windows+WSL box *can* be a 24/7 server, but it's fiddly and dies if the
PC sleeps or you log out fully. A cheap **Raspberry Pi or an old laptop running Ubuntu**
is far cleaner — services + reboot just works, no Windows wrangling.

## The URL changes — and how to make it stable
The free quick-tunnel gets a **new random URL whenever the tunnel restarts** (crash,
reboot, `systemctl --user restart sandbox-tunnel`). `bash deploy/url.sh` always prints
the current one. For a **permanent address that never changes**, you need a
**Cloudflare *named* tunnel**, which requires a domain on Cloudflare (cheap TLDs are
~₹100–800/yr — not strictly free). If you get one, I can switch the tunnel service to a
named tunnel so the URL is fixed forever.

## Manage it
- **Current URL:** `bash deploy/url.sh`
- **Logs:** `journalctl --user -u sandbox-tunnel -f` (or `sandbox-app`)
- **Restart:** `systemctl --user restart sandbox-app sandbox-tunnel`
- **Stop hosting:** `systemctl --user disable --now sandbox-app sandbox-tunnel`
- **Keys:** `python3 scripts/make_key.py --list` (add: `... NAME`, revoke: `... --revoke NAME`)
- **Update code:** `git pull && systemctl --user restart sandbox-app`
- **Keep `runsc` patched** — re-download it periodically; it's your main security job.
