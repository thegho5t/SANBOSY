# Deploy to a public, always-on server (free, on Oracle Cloud)

This gets you a permanent `https://…` URL that stays up 24/7 without your PC, for
$0, on Oracle's **Always Free** tier. You do the account/VM steps (they need your
card for identity verification — Always Free resources are **not** charged); the
`cloud_setup.sh` script does everything on the server.

Works on any root Ubuntu 24.04 VM (Oracle ARM or x86, or another provider) — only
the "create the VM" steps are Oracle-specific.

---

## 1. Create the free VM

1. Sign up at **https://www.oracle.com/cloud/free/** (needs a debit/credit card to
   verify identity; Always Free resources aren't billed). Pick a home region near you.
2. Console → **Compute → Instances → Create Instance**:
   - **Image:** Canonical **Ubuntu 24.04**.
   - **Shape:** **Ampere (ARM) — `VM.Standard.A1.Flex`**, set **2 OCPU / 12 GB** (well
     within Always Free). If you get **"Out of host capacity"**, retry over a few
     hours, try another Availability Domain, or fall back to the x86 **`VM.Standard.E2.1.Micro`**
     (1 GB — works but tight; drop Go if so).
   - **SSH keys:** upload your public key (or let it generate one and download it).
   - Create. Note the **Public IP address**.
3. SSH in: `ssh ubuntu@<PUBLIC_IP>`

## 2. Open the ports (Oracle's #1 gotcha)

Oracle blocks inbound traffic at the **cloud firewall** by default — `ufw` alone is
not enough. In the Console:

- **Networking → Virtual Cloud Networks → (your VCN) → Security Lists → Default** →
  **Add Ingress Rules**:
  - Source `0.0.0.0/0`, IP Protocol **TCP**, Destination port **80**
  - Source `0.0.0.0/0`, IP Protocol **TCP**, Destination port **443**

(The setup script opens the OS firewall for you; this step opens Oracle's.)

## 3. Get a free domain (for HTTPS)

Caddy issues a real Let's Encrypt certificate, which needs a hostname:

1. Go to **https://www.duckdns.org**, sign in, create a subdomain e.g. `sandbosy`.
2. Set its IP to your VM's **Public IP**. You now have `sandbosy.duckdns.org`.

(No domain? You can skip HTTPS and reach it at `http://<PUBLIC_IP>:8000` after opening
port 8000 in the Security List — but HTTPS via DuckDNS is free and worth it.)

## 4. Deploy (one script)

On the VM:
```bash
sudo apt-get update && sudo apt-get install -y git
git clone https://github.com/thegho5t/SANBOSY.git ~/sandbox
cd ~/sandbox
DOMAIN=sandbosy.duckdns.org bash deploy/cloud_setup.sh
```
It installs gVisor + all toolchains, builds the rootfs (~5–10 min), starts the app as
a systemd service, puts Caddy in front for automatic HTTPS, and prints your **public
URL + API keys**. First load may take ~30 s while Caddy fetches the certificate.

## 5. Share

Send each friend **`https://sandbosy.duckdns.org` + one API key** (printed by the
script; re-list with `python3 scripts/make_key.py --list`, add more with
`python3 scripts/make_key.py NAME`). They open the link, paste the key in the welcome
prompt, and run code. It stays up with your PC off. 🎉

---

## Operating it

- **Logs:** `journalctl --user -u sandbox -f`
- **Restart:** `systemctl --user restart sandbox`
- **Update to latest code:** `cd ~/sandbox && git pull && systemctl --user restart sandbox`
- **Watch abuse:** `curl -s localhost:8000/api/v1/abuse -H 'X-API-Key: <key>'`
- **Keep runsc patched** (this is your #1 security job): re-run the runsc install step
  in `cloud_setup.sh` periodically, then `systemctl --user restart sandbox`.
- Hardened defaults (auth required, 20 runs/min, 2 concurrent, abuse quarantine at 15,
  per-identity caches) live in `~/.config/sandbox.env` — edit + restart to tune.

## Notes / gotchas
- **Out of capacity** on ARM is common — retry, change AD/region, or use the x86 micro.
- Oracle may reclaim an Always Free instance left **100% idle** for a long time; light
  real use avoids this.
- The script assumes Ubuntu 24.04 (Go 1.22 / gcc 13 / Python 3.12 / Ruby 3.2). On a
  different release it warns and tells you the one line to adjust (Go's `GOROOT` in
  `app/languages/registry.py`).
