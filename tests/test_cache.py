"""Unit tests for build-cache identity sanitization (multi-tenant hardening)."""
from app.executor.runner import _safe_seg


def test_plain_names_preserved():
    assert _safe_seg("alice") == "alice"
    assert _safe_seg("env-3") == "env-3"
    assert _safe_seg("team_ci") == "team_ci"


def test_path_separators_neutralized():
    # a malicious/odd identity name must never escape the cache root
    assert "/" not in _safe_seg("../../etc")
    assert "\\" not in _safe_seg("a\\b")
    assert ".." not in _safe_seg("..")
    assert _safe_seg("../../etc") == "______etc"


def test_never_empty():
    assert _safe_seg("") == "_"
    assert _safe_seg("/") == "_"


def test_bounded_length():
    assert len(_safe_seg("x" * 500)) <= 64
