"""API-key authentication (Phase 2, additive).

Backward-compatible: if no keys are configured, auth is DISABLED and the service
behaves exactly as in Phase 1 (single-operator local use). Once one or more keys
exist, protected routes require a valid key.

Keys are stored only as SHA-256 hashes, so the store file is not itself a secret.
A request's resolved identity (the key's name) is returned by the dependency and
stashed on request.state so future middleware (rate limits, quotas, abuse
detection) can key off it without changing this contract.
"""
import hashlib
import hmac
import json
import os
from dataclasses import dataclass
from pathlib import Path

from fastapi import Depends, HTTPException, Request
from fastapi.security import APIKeyHeader

RUNTIME_ROOT = Path(os.environ.get("SANDBOX_RUNTIME_ROOT",
                                   str(Path.home() / ".sandbox")))
KEYS_FILE = RUNTIME_ROOT / "api_keys.json"

# auto_error=False: we handle the missing-key case ourselves so auth-disabled
# mode doesn't 403 on a missing header.
_header = APIKeyHeader(name="X-API-Key", auto_error=False)


@dataclass(frozen=True)
class Identity:
    name: str            # "local" when auth is disabled
    authenticated: bool
    role: str = "user"   # "admin" (operator/superuser) or "user" (basic)

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _load_store() -> list[dict]:
    """Registered keys: [{"name", "hash", "created"}]. Merged with any raw keys
    from SANDBOX_API_KEYS (comma-separated), hashed on load."""
    entries: list[dict] = []
    if KEYS_FILE.exists():
        try:
            entries = json.loads(KEYS_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            entries = []
    env = os.environ.get("SANDBOX_API_KEYS", "").strip()
    for i, raw in enumerate(k for k in env.split(",") if k.strip()):
        entries.append({"name": f"env-{i}", "hash": hash_key(raw.strip())})
    return entries


def auth_enabled() -> bool:
    return len(_load_store()) > 0


def _match(raw: str) -> dict | None:
    """Return the matching key entry (name + role), or None. Constant-time
    compare against every stored hash so timing doesn't leak which keys exist."""
    candidate = hash_key(raw)
    matched: dict | None = None
    for entry in _load_store():
        if hmac.compare_digest(candidate, entry.get("hash", "")):
            matched = entry
    return matched


async def require_api_key(request: Request,
                          key: str | None = Depends(_header)) -> Identity:
    if not auth_enabled():
        # single-operator local use: full (admin) access, no key needed
        ident = Identity(name="local", authenticated=False, role="admin")
        request.state.identity = ident
        return ident
    if not key:
        raise HTTPException(status_code=401, detail="missing API key",
                            headers={"WWW-Authenticate": "X-API-Key"})
    entry = _match(key)
    if entry is None:
        raise HTTPException(status_code=401, detail="invalid API key")
    ident = Identity(name=entry.get("name", "unknown"), authenticated=True,
                     role=entry.get("role", "user"))
    request.state.identity = ident
    return ident


async def require_admin(identity: "Identity" = Depends(require_api_key)) -> Identity:
    """Gate a route to admin (superuser) identities only."""
    if not identity.is_admin:
        raise HTTPException(status_code=403, detail="admin privilege required")
    return identity
