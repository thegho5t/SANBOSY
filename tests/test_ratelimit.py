"""Unit tests for the per-identity rate limiter."""
import pytest

from app.api.ratelimit import RateLimiter, RateLimited


def test_disabled_never_limits():
    rl = RateLimiter(per_min=0, max_inflight=0)
    assert rl.enabled() is False
    for _ in range(100):
        rl.acquire("id")
        rl.release("id")


def test_request_rate_cap():
    rl = RateLimiter(per_min=3, max_inflight=0)
    for _ in range(3):
        rl.acquire("id")
        rl.release("id")
    with pytest.raises(RateLimited) as e:
        rl.acquire("id")
    assert e.value.retry_after >= 1


def test_concurrency_cap():
    rl = RateLimiter(per_min=1000, max_inflight=2)
    rl.acquire("id")
    rl.acquire("id")
    with pytest.raises(RateLimited):
        rl.acquire("id")           # 3rd concurrent rejected
    rl.release("id")
    rl.acquire("id")               # slot freed


def test_identities_are_independent():
    rl = RateLimiter(per_min=1, max_inflight=0)
    rl.acquire("a")
    rl.release("a")
    rl.acquire("b")                # b unaffected by a's usage
    rl.release("b")


def test_gc_drops_idle_identities():
    rl = RateLimiter(per_min=0, max_inflight=2)
    rl.acquire("id")
    rl.release("id")
    # no in-flight and no rate events -> bookkeeping removed
    assert "id" not in rl._inflight
    assert "id" not in rl._events
