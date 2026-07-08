"""Unit tests for the job queue (fake execute; no gVisor)."""
import asyncio

import pytest

from app.executor import jobqueue
from app.executor.jobqueue import JobQueue, QueueFull
from app.executor.runner import ExecutionRequest, ExecutionResult

REQ = ExecutionRequest(args=["/bin/true"])


def _fast_execute(monkeypatch):
    async def fake(req):
        return ExecutionResult(stdout="done\n", exit_code=0)
    monkeypatch.setattr(jobqueue, "execute", fake)


async def test_sync_submit_returns_result(monkeypatch):
    _fast_execute(monkeypatch)
    q = JobQueue(workers=2, depth=4)
    await q.start()
    try:
        res = await q.submit(REQ, "local")
        assert res.exit_code == 0 and res.stdout == "done\n"
    finally:
        await q.stop(grace_s=5)


async def test_async_lifecycle(monkeypatch):
    _fast_execute(monkeypatch)
    q = JobQueue(workers=2, depth=4)
    await q.start()
    try:
        jid = await q.submit_async(REQ, "local")
        for _ in range(50):
            job = q.get_job(jid, "local")
            if job.status == "done":
                break
            await asyncio.sleep(0.02)
        assert q.get_job(jid, "local").status == "done"
        assert q.get_job(jid, "local").result.exit_code == 0
        # identity-scoped: another identity can't see it
        assert q.get_job(jid, "other") is None
    finally:
        await q.stop(grace_s=5)


async def test_backpressure_returns_queue_full(monkeypatch):
    gate = asyncio.Event()

    async def blocked(req):
        await gate.wait()
        return ExecutionResult(exit_code=0)
    monkeypatch.setattr(jobqueue, "execute", blocked)

    q = JobQueue(workers=1, depth=1)
    await q.start()
    try:
        await q.submit_async(REQ, "local")   # taken by the worker (blocks)
        await asyncio.sleep(0.05)
        await q.submit_async(REQ, "local")   # fills the depth-1 queue
        with pytest.raises(QueueFull):
            await q.submit_async(REQ, "local")  # over capacity
    finally:
        gate.set()
        await q.stop(grace_s=5)


async def test_closing_rejects_new_work(monkeypatch):
    _fast_execute(monkeypatch)
    q = JobQueue(workers=1, depth=2)
    await q.start()
    stop = asyncio.create_task(q.stop(grace_s=5))
    await asyncio.sleep(0.05)             # let stop() set the closing flag
    with pytest.raises(QueueFull):
        await q.submit_async(REQ, "local")
    await stop


async def test_graceful_drain_completes_inflight(monkeypatch):
    gate = asyncio.Event()
    done = {}

    async def blocked(req):
        await gate.wait()
        return ExecutionResult(stdout="drained\n", exit_code=0)
    monkeypatch.setattr(jobqueue, "execute", blocked)

    async def on_complete(job):
        done[job.id] = job.status

    q = JobQueue(workers=1, depth=2)
    await q.start()
    jid = await q.submit_async(REQ, "local", on_complete)
    await asyncio.sleep(0.05)             # worker picks it up
    stop = asyncio.create_task(q.stop(grace_s=5))
    await asyncio.sleep(0.05)
    gate.set()                            # let the in-flight job finish
    await stop
    assert done.get(jid) == "done"
