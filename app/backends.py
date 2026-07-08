"""Optional Redis-backed shared state for horizontal scale (Phase 2+).

The in-memory `RateLimiter` and `AbuseTracker` are per-process, so per-identity
caps only hold within one node. When `SANDBOX_REDIS_URL` is set these Redis-backed
equivalents share that state across nodes, keeping the same (synchronous) interface
so nothing else changes. The job queue stays per-node (a load balancer distributes;
each node runs its own worker pool), and run history is already a SQLite file that
can be pointed at shared storage.

Redis is entirely optional — unset `SANDBOX_REDIS_URL` and the process falls back to
the dependency-free in-memory backends. The rate-limit check is a single atomic Lua
script so concurrent nodes can't jointly exceed a cap. (Calls are synchronous; for a
LAN Redis they add sub-millisecond latency to a request — a fully async backend is a
future optimization.)
"""
import os
import time
import uuid

from .abuse import Verdict, WINDOW_S as ABUSE_WINDOW_S, THRESHOLD as ABUSE_THRESHOLD
from .api.ratelimit import RateLimited, WINDOW_S as RL_WINDOW_S

REDIS_URL = os.environ.get("SANDBOX_REDIS_URL", "")


def enabled() -> bool:
    return bool(REDIS_URL)


def _client():
    import redis  # imported lazily so redis is only needed when configured
    return redis.Redis.from_url(REDIS_URL, decode_responses=True)


# --- rate limiter -----------------------------------------------------------

# KEYS[1]=events zset, KEYS[2]=inflight counter
# ARGV: now, window, per_min, max_inflight, member-token
_RL_ACQUIRE = """
local now=tonumber(ARGV[1]); local win=tonumber(ARGV[2])
local per=tonumber(ARGV[3]); local maxin=tonumber(ARGV[4]); local tok=ARGV[5]
if per>0 then
  redis.call('ZREMRANGEBYSCORE', KEYS[1], 0, now-win)
  if redis.call('ZCARD', KEYS[1]) >= per then
    local o=redis.call('ZRANGE', KEYS[1], 0, 0, 'WITHSCORES')
    local retry=win
    if o[2] then retry=math.ceil(tonumber(o[2])+win-now) end
    return {0, math.max(1, retry), 'request rate limit exceeded'}
  end
end
local inf=tonumber(redis.call('GET', KEYS[2]) or '0')
if maxin>0 and inf>=maxin then return {0, 1, 'too many concurrent runs'} end
if per>0 then
  redis.call('ZADD', KEYS[1], now, tok)
  redis.call('EXPIRE', KEYS[1], math.ceil(win)+1)
end
redis.call('INCR', KEYS[2]); redis.call('EXPIRE', KEYS[2], 3600)
return {1, 0, ''}
"""

_RL_RELEASE = """
if tonumber(redis.call('GET', KEYS[1]) or '0') > 0 then redis.call('DECR', KEYS[1]) end
return 1
"""


class RedisRateLimiter:
    def __init__(self, per_min: int | None = None, max_inflight: int | None = None):
        self.per_min = (per_min if per_min is not None
                        else int(os.environ.get("SANDBOX_RATE_PER_MIN", "0")))
        self.max_inflight = (max_inflight if max_inflight is not None
                             else int(os.environ.get("SANDBOX_MAX_INFLIGHT", "0")))
        self.window_s = RL_WINDOW_S
        self._r = _client()
        self._acquire = self._r.register_script(_RL_ACQUIRE)
        self._release = self._r.register_script(_RL_RELEASE)

    def enabled(self) -> bool:
        return self.per_min > 0 or self.max_inflight > 0

    def acquire(self, identity: str) -> None:
        if not self.enabled():
            return
        ok, retry, reason = self._acquire(
            keys=[f"rl:ev:{identity}", f"rl:inf:{identity}"],
            args=[time.time(), self.window_s, self.per_min, self.max_inflight,
                  f"{time.time()}:{uuid.uuid4().hex}"])
        if not int(ok):
            raise RateLimited(int(retry), reason)

    def release(self, identity: str) -> None:
        if not self.enabled():
            return
        self._release(keys=[f"rl:inf:{identity}"])

    def stats(self) -> dict:
        return {"enabled": self.enabled(), "per_min": self.per_min,
                "max_inflight": self.max_inflight, "backend": "redis"}


# --- abuse tracker ----------------------------------------------------------

# member format: "weight|flag1,flag2|token"; score = timestamp
_AB_RECORD = """
local now=tonumber(ARGV[1]); local win=tonumber(ARGV[2])
redis.call('ZREMRANGEBYSCORE', KEYS[1], 0, now-win)
if ARGV[5]=='1' then
  redis.call('ZADD', KEYS[1], now, ARGV[3]..'|'..ARGV[4]..'|'..ARGV[6])
  redis.call('EXPIRE', KEYS[1], math.ceil(win)+1)
end
local sum=0
for _,m in ipairs(redis.call('ZRANGE', KEYS[1], 0, -1)) do
  sum = sum + tonumber(string.match(m, '^(%d+)'))
end
return sum
"""


class RedisAbuseTracker:
    def __init__(self, threshold: int | None = None, window_s: float | None = None):
        self.threshold = threshold if threshold is not None else ABUSE_THRESHOLD
        self.window_s = window_s if window_s is not None else ABUSE_WINDOW_S
        self._r = _client()
        self._record = self._r.register_script(_AB_RECORD)

    def record(self, identity: str, verdict: Verdict) -> int:
        return int(self._record(
            keys=[f"ab:{identity}"],
            args=[time.time(), self.window_s, verdict.weight,
                  ",".join(verdict.flags), "1" if verdict.suspicious else "0",
                  uuid.uuid4().hex]))

    def score(self, identity: str) -> int:
        # record with a non-suspicious verdict just prunes and returns the sum
        return self.record(identity, Verdict(False, [], 0))

    def is_quarantined(self, identity: str) -> bool:
        return self.threshold > 0 and self.score(identity) >= self.threshold

    def report(self) -> dict:
        out = {}
        now = time.time()
        for key in self._r.scan_iter(match="ab:*", count=100):
            identity = key.split(":", 1)[1]
            self._r.zremrangebyscore(key, 0, now - self.window_s)
            members = self._r.zrange(key, 0, -1)
            if not members:
                continue
            score = 0
            flag_counts: dict[str, int] = {}
            for m in members:
                parts = m.split("|")
                score += int(parts[0])
                for f in (parts[1].split(",") if len(parts) > 1 and parts[1] else []):
                    flag_counts[f] = flag_counts.get(f, 0) + 1
            out[identity] = {"score": score, "events": len(members),
                             "flags": flag_counts,
                             "quarantined": self.threshold > 0 and score >= self.threshold}
        return {"threshold": self.threshold, "window_s": self.window_s,
                "identities": out, "backend": "redis"}
