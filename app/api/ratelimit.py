"""Per-identity rate limiting (Phase 2, additive).

Two independent caps, both keyed on the authenticated identity:
  * request rate  — a sliding 60s window, SANDBOX_RATE_PER_MIN requests/min
  * concurrency   — SANDBOX_MAX_INFLIGHT simultaneous in-flight jobs

Either cap is disabled when its env var is 0/unset, so local single-operator use
is unthrottled by default. In-memory and lock-free: all access happens on the
single asyncio event-loop thread. State is per-process (fine for Phase 1's single
node; a shared store like Redis is the horizontal-scale attach point).
"""
import os
import time
from collections import defaultdict, deque

WINDOW_S = 60.0


class RateLimited(Exception):
    def __init__(self, retry_after: int, reason: str):
        super().__init__(reason)
        self.retry_after = retry_after
        self.reason = reason


class RateLimiter:
    def __init__(self, per_min: int | None = None, max_inflight: int | None = None):
        self.per_min = (per_min if per_min is not None
                        else int(os.environ.get("SANDBOX_RATE_PER_MIN", "0")))
        self.max_inflight = (max_inflight if max_inflight is not None
                             else int(os.environ.get("SANDBOX_MAX_INFLIGHT", "0")))
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._inflight: dict[str, int] = defaultdict(int)

    def enabled(self) -> bool:
        return self.per_min > 0 or self.max_inflight > 0

    def acquire(self, identity: str) -> None:
        """Admit one request for `identity` or raise RateLimited. On success the
        caller MUST pair this with release() in a finally block."""
        now = time.monotonic()
        if self.per_min > 0:
            window = self._events[identity]
            while window and now - window[0] >= WINDOW_S:
                window.popleft()
            if len(window) >= self.per_min:
                retry = max(1, int(WINDOW_S - (now - window[0])))
                raise RateLimited(retry, "request rate limit exceeded")
        if self.max_inflight > 0 and self._inflight[identity] >= self.max_inflight:
            raise RateLimited(1, "too many concurrent runs")
        if self.per_min > 0:
            self._events[identity].append(now)
        self._inflight[identity] += 1

    def release(self, identity: str) -> None:
        if self._inflight.get(identity, 0) > 0:
            self._inflight[identity] -= 1
        self._gc(identity)

    def _gc(self, identity: str) -> None:
        """Drop an identity's bookkeeping once it has no recent events and no
        in-flight runs, so the maps don't grow without bound over many callers."""
        if self._inflight.get(identity, 0) <= 0:
            self._inflight.pop(identity, None)
            window = self._events.get(identity)
            if not window:
                self._events.pop(identity, None)

    def stats(self) -> dict:
        return {
            "enabled": self.enabled(),
            "per_min": self.per_min,
            "max_inflight": self.max_inflight,
        }
