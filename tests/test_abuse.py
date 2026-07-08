"""Unit tests for abuse classification and scoring."""
from app.abuse import AbuseTracker, Verdict, classify


def _res(**kw):
    base = dict(stdout="", stderr="", exit_code=0, timed_out=False, error=None)
    base.update(kw)
    return base


def test_clean_run_not_suspicious():
    v = classify(_res(stdout="ok\n"))
    assert v.suspicious is False and v.flags == []


def test_timeout_flagged():
    v = classify(_res(timed_out=True, exit_code=None))
    assert "timeout" in v.flags


def test_network_probe_flagged():
    v = classify(_res(stderr="OSError: [Errno 101] Network is unreachable"))
    assert "network_probe" in v.flags


def test_memory_abuse_flagged():
    v = classify(_res(stderr="MemoryError"))
    assert "memory_abuse" in v.flags


def test_multiple_flags_sum_weight():
    v = classify(_res(timed_out=True, stderr="network is unreachable"))
    assert set(v.flags) == {"timeout", "network_probe"}
    assert v.weight == 2 + 3


def test_tracker_scores_and_quarantines():
    t = AbuseTracker(threshold=5, window_s=300)
    assert t.is_quarantined("id") is False
    t.record("id", Verdict(True, ["network_probe"], 3))
    assert t.is_quarantined("id") is False
    t.record("id", Verdict(True, ["timeout"], 2))
    assert t.score("id") == 5
    assert t.is_quarantined("id") is True


def test_threshold_zero_never_quarantines():
    t = AbuseTracker(threshold=0, window_s=300)
    for _ in range(10):
        t.record("id", Verdict(True, ["network_probe"], 3))
    assert t.score("id") == 30
    assert t.is_quarantined("id") is False


def test_report_shape():
    t = AbuseTracker(threshold=5, window_s=300)
    t.record("id", Verdict(True, ["timeout"], 2))
    rep = t.report()
    assert rep["threshold"] == 5
    assert rep["identities"]["id"]["score"] == 2
    assert rep["identities"]["id"]["flags"]["timeout"] == 1


def test_gc_drops_empty_identity():
    t = AbuseTracker(threshold=5, window_s=0.0)  # everything expires immediately
    t.record("id", Verdict(True, ["timeout"], 2))
    assert t.score("id") == 0          # prune fires
    assert "id" not in t._events
