"""Unit tests for API-key auth primitives."""
import json

from app.api import auth


def test_hash_is_deterministic_and_hex():
    h = auth.hash_key("sk_secret")
    assert h == auth.hash_key("sk_secret")
    assert len(h) == 64 and all(c in "0123456789abcdef" for c in h)


def test_auth_disabled_by_default(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "KEYS_FILE", tmp_path / "none.json")
    monkeypatch.delenv("SANDBOX_API_KEYS", raising=False)
    assert auth.auth_enabled() is False
    assert auth._match("anything") is None


def test_match_valid_and_invalid(tmp_path, monkeypatch):
    kf = tmp_path / "keys.json"
    kf.write_text(json.dumps([{"name": "op", "hash": auth.hash_key("sk_good")}]))
    monkeypatch.setattr(auth, "KEYS_FILE", kf)
    monkeypatch.delenv("SANDBOX_API_KEYS", raising=False)
    assert auth.auth_enabled() is True
    # _match returns the matching key entry (name + role) or None
    assert auth._match("sk_good")["name"] == "op"
    assert auth._match("sk_bad") is None


def test_env_keys_supported(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "KEYS_FILE", tmp_path / "none.json")
    monkeypatch.setenv("SANDBOX_API_KEYS", "sk_env1, sk_env2")
    assert auth.auth_enabled() is True
    assert auth._match("sk_env1") is not None
    assert auth._match("sk_env2") is not None
    assert auth._match("sk_missing") is None
