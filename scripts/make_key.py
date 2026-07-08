"""Generate an API key, store its hash, and print the raw key ONCE.

Usage: python3 scripts/make_key.py <name>
       python3 scripts/make_key.py --list
       python3 scripts/make_key.py --revoke <name>

The raw key is shown only at creation time; only its SHA-256 hash is stored.
"""
import argparse
import datetime
import json
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.api.auth import KEYS_FILE, hash_key  # noqa: E402


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
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--revoke", metavar="NAME")
    args = ap.parse_args()

    entries = _load()

    if args.list:
        for e in entries:
            print(f"{e['name']:20} created={e.get('created', '?')}")
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

    raw = "sk_" + secrets.token_urlsafe(32)
    entries.append({
        "name": args.name,
        "hash": hash_key(raw),
        "created": datetime.date.today().isoformat(),
    })
    _save(entries)
    print(f"API key for {args.name!r} (shown once, store it now):\n\n  {raw}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
