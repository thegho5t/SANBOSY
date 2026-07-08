"""Unit tests for the optional Redis-backed shared state (skipped without Redis)."""
import os

import pytest

pytest.importorskip("redis")
from app import backends  # noqa: E402
from app.abuse import Verdict  # noqa: E402
from app.api.ratelimit import RateLimited  # noqa: E402

URL = os.environ.get("SANDBOX_REDIS_URL", "redis://localhost:6379")


@pytest.fixture
def redis_env(monkeypatch):
    monkeypatch.setattr(backends, "REDIS_URL", URL)
    try:
        c = backends._client()
        c.ping()
        c.flushdb()
    except Exception:
        pytest.skip("no reachable Redis server")
    return c


def test_enabled_reflects_url(redis_env):
    assert backends.enabled() is True


def test_redis_request_rate_cap(redis_env):
    rl = backends.RedisRateLimiter(per_min=3, max_inflight=0)
    ok = blocked = 0
    for _ in range(6):
        try:
            rl.acquire("u"); rl.release("u"); ok += 1
        except RateLimited:
            blocked += 1
    assert ok == 3 and blocked == 3


def test_redis_concurrency_cap(redis_env):
    rl = backends.RedisRateLimiter(per_min=0, max_inflight=2)
    rl.acquire("u"); rl.acquire("u")
    with pytest.raises(RateLimited):
        rl.acquire("u")
    rl.release("u")
    rl.acquire("u")            # slot freed


def test_redis_abuse_scoring_and_quarantine(redis_env):
    ab = backends.RedisAbuseTracker(threshold=5, window_s=300)
    assert ab.is_quarantined("u") is False
    ab.record("u", Verdict(True, ["network_probe"], 3))
    ab.record("u", Verdict(True, ["timeout"], 2))
    assert ab.score("u") == 5
    assert ab.is_quarantined("u") is True
    rep = ab.report()["identities"]["u"]
    assert rep["score"] == 5 and rep["flags"]["timeout"] == 1


def test_redis_stats_marks_backend(redis_env):
    assert backends.RedisRateLimiter().stats()["backend"] == "redis"
