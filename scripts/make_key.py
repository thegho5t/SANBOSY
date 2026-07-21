"""Generate an API key, store its hash, and print the raw key ONCE.

Usage: python3 scripts/make_key.py <name> [--role admin|user]
       python3 scripts/make_key.py --list
       python3 scripts/make_key.py --revoke <name>

The raw key is shown only at creation time; only its SHA-256 hash is stored.
Roles: 'admin' is the operator/superuser (not rate-limited, sees /abuse);
'user' is a basic account. At most MAX_USERS basic users may exist at once;
admins are unlimited and don't count toward that cap.
"""
import argparse
import datetime
import json
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.api.auth import KEYS_FILE, hash_key  # noqa: E402

MAX_USERS = 2  # cap on basic (non-admin) accounts


def _load() -> list[dict]:
    if KEYS_FILE.exists():
        return json.loads(KEYS_FILE.read_text())
    return []


def _save(entries: list[dict]) -> None:
    KEYS_FILE.parent.mkdir(parents=True, exist_ok=True)
    KEYS_FILE.write_text(json.dumps(entries, indent=2))
    KEYS_FILE.chmod(0o600)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("name", nargs="?", help="label for the new key")
    ap.add_argument("--role", choices=("admin", "user"), default="user",
                    help="admin = operator/superuser; user = basic (default)")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--revoke", metavar="NAME")
    args = ap.parse_args()

    entries = _load()

    if args.list:
        for e in entries:
            print(f"{e['name']:20} role={e.get('role', 'user'):6} "
                  f"created={e.get('created', '?')}")
        if not entries:
            print("(no keys; auth is DISABLED)")
        return 0

    if args.revoke:
        kept = [e for e in entries if e["name"] != args.revoke]
        if len(kept) == len(entries):
            print(f"no key named {args.revoke!r}")
            return 1
        _save(kept)
        print(f"revoked {args.revoke!r}")
        return 0

    if not args.name:
        ap.error("provide a name, or use --list / --revoke")
    if any(e["name"] == args.name for e in entries):
        print(f"key named {args.name!r} already exists")
        return 1
    if args.role == "user":
        n_users = sum(1 for e in entries if e.get("role", "user") != "admin")
        if n_users >= MAX_USERS:
            print(f"user limit reached ({MAX_USERS} basic users). "
                  f"Revoke one first, or create an admin with --role admin.")
            return 1

    raw = "sk_" + secrets.token_urlsafe(32)
    entries.append({
        "name": args.name,
        "hash": hash_key(raw),
        "role": args.role,
        "created": datetime.date.today().isoformat(),
    })
    _save(entries)
    print(f"API key for {args.name!r} (role={args.role}; shown once, "
          f"store it now):\n\n  {raw}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
