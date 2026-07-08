"""Unit tests for the build-cache size cap (security-review finding)."""
from app.executor import runner


def _make_cache(root, name, size_bytes):
    d = root / "cache" / name
    d.mkdir(parents=True)
    (d / "blob").write_bytes(b"A" * size_bytes)
    return d


def test_evicts_oversized_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "CACHE_DIR", tmp_path / "cache")
    big = _make_cache(tmp_path, "go", 5 * 1024 * 1024)
    small = _make_cache(tmp_path, "rust", 1 * 1024 * 1024)

    evicted = runner.enforce_cache_limit(max_bytes=2 * 1024 * 1024)
    assert evicted == 1
    assert not big.exists()      # over cap -> dropped (will re-warm)
    assert small.exists()        # under cap -> kept


def test_no_eviction_under_cap(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "CACHE_DIR", tmp_path / "cache")
    _make_cache(tmp_path, "go", 1 * 1024 * 1024)
    assert runner.enforce_cache_limit(max_bytes=10 * 1024 * 1024) == 0


def test_missing_cache_dir_is_noop(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "CACHE_DIR", tmp_path / "nope")
    assert runner.enforce_cache_limit(max_bytes=1) == 0


def test_disabled_when_max_zero(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "CACHE_DIR", tmp_path / "cache")
    _make_cache(tmp_path, "go", 5 * 1024 * 1024)
    assert runner.enforce_cache_limit(max_bytes=0) == 0
