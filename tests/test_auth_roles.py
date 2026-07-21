"""Unit tests for API-key roles (admin/superuser) and the basic-user cap.
No gVisor needed — these exercise auth resolution and make_key directly."""
import json

import pytest


@pytest.fixture
def keys_file(tmp_path, monkeypatch):
    """Point auth + make_key at a temp key store, auth ON."""
    from app.api import auth
    f = tmp_path / "api_keys.json"
    monkeypatch.setattr(auth, "KEYS_FILE", f)
    monkeypatch.delenv("SANDBOX_API_KEYS", raising=False)
    return f


def _write(f, entries):
    f.write_text(json.dumps(entries))


async def test_admin_key_resolves_to_admin_identity(keys_file):
    from app.api import auth
    _write(keys_file, [
        {"name": "mohit", "hash": auth.hash_key("sk_admin"), "role": "admin"},
        {"name": "user1", "hash": auth.hash_key("sk_user"), "role": "user"},
    ])
    admin = await auth.require_api_key(_req(), key="sk_admin")
    assert admin.name == "mohit" and admin.is_admin

    user = await auth.require_api_key(_req(), key="sk_user")
    assert user.name == "user1" and not user.is_admin

    # a key with no role field defaults to a basic user (back-compat)
    _write(keys_file, [{"name": "old", "hash": auth.hash_key("sk_old")}])
    old = await auth.require_api_key(_req(), key="sk_old")
    assert old.role == "user" and not old.is_admin


async def test_require_admin_rejects_basic_user(keys_file):
    from fastapi import HTTPException
    from app.api import auth
    _write(keys_file, [
        {"name": "u", "hash": auth.hash_key("sk_u"), "role": "user"}])
    user = await auth.require_api_key(_req(), key="sk_u")
    with pytest.raises(HTTPException) as ei:
        await auth.require_admin(user)
    assert ei.value.status_code == 403


def test_make_key_caps_basic_users_but_not_admins(keys_file, monkeypatch, capsys):
    import importlib
    mk = importlib.import_module("scripts.make_key")
    monkeypatch.setattr(mk, "KEYS_FILE", keys_file)

    def run(*argv):
        monkeypatch.setattr("sys.argv", ["make_key.py", *argv])
        return mk.main()

    assert run("user1") == 0
    assert run("user2") == 0
    # third basic user is refused
    assert run("user3") == 1
    assert "user limit reached" in capsys.readouterr().out
    # but an admin can still be created
    assert run("mohit", "--role", "admin") == 0
    roles = {e["name"]: e.get("role", "user")
             for e in json.loads(keys_file.read_text())}
    assert roles == {"user1": "user", "user2": "user", "mohit": "admin"}


def _req():
    """A throwaway object with a .state to satisfy require_api_key."""
    class _S:  # noqa: D401
        pass
    r = _S()
    r.state = _S()
    return r
