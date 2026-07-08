"""Unit tests for the SQLite run-history store (isolated temp DB)."""


def _result(**kw):
    base = dict(stdout="hi\n", stderr="", exit_code=0, timed_out=False,
                truncated_stdout=False, truncated_stderr=False,
                wall_time_ms=10, stage="run", error=None)
    base.update(kw)
    return base


def _record(store, identity="local", **kw):
    return store.RunRecord(
        identity=identity, language="python",
        files={"main.py": "print(1)"}, stdin="",
        run_timeout_ms=None, result=_result(**kw))


async def test_save_and_get(fresh_store):
    rec = _record(fresh_store)
    await fresh_store.save(rec)
    got = await fresh_store.get_run("local", rec.id)
    assert got is not None
    assert got["language"] == "python"
    assert got["files"] == {"main.py": "print(1)"}
    assert got["stdout"] == "hi\n"


async def test_list_orders_and_filters_by_identity(fresh_store):
    await fresh_store.save(_record(fresh_store, identity="alice"))
    await fresh_store.save(_record(fresh_store, identity="bob"))
    alice = await fresh_store.list_runs("alice")
    bob = await fresh_store.list_runs("bob")
    assert len(alice) == 1 and len(bob) == 1
    # an identity cannot see another's run
    assert await fresh_store.get_run("bob", alice[0]["id"]) is None


async def test_delete_is_identity_scoped(fresh_store):
    rec = _record(fresh_store, identity="alice")
    await fresh_store.save(rec)
    assert await fresh_store.delete_run("bob", rec.id) is False   # not bob's
    assert await fresh_store.delete_run("alice", rec.id) is True
    assert await fresh_store.get_run("alice", rec.id) is None


async def test_suspicious_flags_roundtrip(fresh_store):
    rec = fresh_store.RunRecord(
        identity="local", language="python", files={"main.py": "x"}, stdin="",
        run_timeout_ms=None, result=_result(timed_out=True, exit_code=None),
        suspicious=True, flags=["timeout", "network_probe"])
    await fresh_store.save(rec)
    got = await fresh_store.get_run("local", rec.id)
    assert got["suspicious"] is True
    assert got["flags"] == ["timeout", "network_probe"]


async def test_persist_disabled_is_noop(fresh_store, monkeypatch):
    monkeypatch.setattr(fresh_store, "PERSIST_ENABLED", False)
    rec = _record(fresh_store)
    await fresh_store.save(rec)          # silently does nothing
    assert await fresh_store.list_runs("local") == []
