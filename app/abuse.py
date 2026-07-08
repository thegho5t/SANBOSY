"""Abuse detection (Phase 2, additive).

Heuristic — it classifies each finished run for hostile-looking signals and keeps
a rolling per-identity abuse score. Detection is always on (cheap, observational);
auto-quarantine is OFF unless SANDBOX_ABUSE_THRESHOLD > 0, so a single operator is
never surprised by being blocked. When a threshold is set and an identity's score
in the window exceeds it, that identity is quarantined (403) until the window
clears.

Signals are deliberately conservative to limit false positives:
  * timeout        — infinite-loop / busy-spin (result.timed_out)
  * network_probe  — connection attempts, though the sandbox has no network at all,
                     so any such attempt is deliberate
  * memory_abuse   — allocation-failure markers (OOM pressure)
  * orchestration  — an internal execution error

In-memory per-process (a shared store is the horizontal-scale attach point).
"""
import os
import time
from collections import defaultdict, deque
from dataclasses import dataclass

WINDOW_S = float(os.environ.get("SANDBOX_ABUSE_WINDOW_S", "300"))
THRESHOLD = int(os.environ.get("SANDBOX_ABUSE_THRESHOLD", "0"))  # 0 = never block

# Substrings scanned (lowercased) in stderr. Network is fully disabled in the
# sandbox, so a connection error means code tried to reach the network.
_NETWORK_MARKERS = (
    "network is unreachable", "enetunreach", "errno 101", "getaddrinfo",
    "no route to host", "econnrefused", "connection refused",
    "temporary failure in name resolution",
)
_MEMORY_MARKERS = (
    "memoryerror", "cannot allocate memory", "std::bad_alloc",
    "out of memory", "enomem",
)

_WEIGHTS = {"timeout": 2, "network_probe": 3, "memory_abuse": 2, "orchestration": 1}


@dataclass
class Verdict:
    suspicious: bool
    flags: list[str]
    weight: int


def classify(result: dict) -> Verdict:
    flags: list[str] = []
    if result.get("timed_out"):
        flags.append("timeout")
    stderr = (result.get("stderr") or "").lower()
    if any(m in stderr for m in _NETWORK_MARKERS):
        flags.append("network_probe")
    if any(m in stderr for m in _MEMORY_MARKERS):
        flags.append("memory_abuse")
    if result.get("error"):
        flags.append("orchestration")
    weight = sum(_WEIGHTS.get(f, 1) for f in flags)
    return Verdict(suspicious=bool(flags), flags=flags, weight=weight)


class AbuseTracker:
    def __init__(self, threshold: int = THRESHOLD, window_s: float = WINDOW_S):
        self.threshold = threshold
        self.window_s = window_s
        # identity -> deque[(ts, weight, flags)]
        self._events: dict[str, deque] = defaultdict(deque)

    def _prune(self, identity: str, now: float) -> None:
        """Expire old events; drop the identity entirely once empty so the map
        doesn't grow without bound over many callers."""
        window = self._events.get(identity)
        if window is None:
            return
        while window and now - window[0][0] >= self.window_s:
            window.popleft()
        if not window:
            del self._events[identity]

    def record(self, identity: str, verdict: Verdict) -> int:
        """Record a run's verdict; return the identity's current window score."""
        now = time.monotonic()
        self._prune(identity, now)
        if verdict.suspicious:
            self._events[identity].append((now, verdict.weight, verdict.flags))
        return sum(w for _, w, _ in self._events.get(identity, ()))

    def score(self, identity: str) -> int:
        self._prune(identity, time.monotonic())
        return sum(w for _, w, _ in self._events.get(identity, ()))

    def is_quarantined(self, identity: str) -> bool:
        return self.threshold > 0 and self.score(identity) >= self.threshold

    def report(self) -> dict:
        now = time.monotonic()
        out = {}
        for identity in list(self._events):
            self._prune(identity, now)
            window = self._events.get(identity)
            if not window:
                continue
            flag_counts: dict[str, int] = {}
            for _, _, flags in window:
                for f in flags:
                    flag_counts[f] = flag_counts.get(f, 0) + 1
            out[identity] = {
                "score": sum(w for _, w, _ in window),
                "events": len(window),
                "flags": flag_counts,
                "quarantined": self.is_quarantined(identity),
            }
        return {
            "threshold": self.threshold,
            "window_s": self.window_s,
            "identities": out,
        }
