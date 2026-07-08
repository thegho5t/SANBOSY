"""Bounded job queue drained by a fixed worker pool (Phase 2).

Replaces the in-process concurrency semaphore. A fixed number of workers pull
jobs off a bounded FIFO queue and run them through `execute()`. Overload produces
backpressure (QueueFull -> HTTP 429) instead of an unbounded pile-up of in-flight
sandboxes. In-process and dependency-free by design (self-hostable constraint).

Two submission modes over the same worker pool:
  * submit()       — synchronous: the caller awaits the result (POST /execute).
  * submit_async() — returns a job id immediately; the caller polls get_job()
                     for status/result (POST /jobs, GET /jobs/{id}). A worker
                     runs an optional on_complete hook so the async path can do
                     its own finalization (persist, abuse scoring, rate release).

Completed async jobs are retained for SANDBOX_JOB_TTL_S so clients have time to
poll, then pruned. Horizontal scale = move this registry to a shared store.
"""
import asyncio
import os
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from .runner import ExecutionRequest, ExecutionResult, execute

WORKERS = int(os.environ.get("SANDBOX_WORKERS", "4"))
QUEUE_DEPTH = int(os.environ.get("SANDBOX_QUEUE_DEPTH", "32"))
JOB_TTL_S = float(os.environ.get("SANDBOX_JOB_TTL_S", "300"))
SHUTDOWN_GRACE_S = float(os.environ.get("SANDBOX_SHUTDOWN_GRACE_S", "35"))


class QueueFull(Exception):
    """Raised when the queue is at capacity (or shutting down); caller -> 429."""


@dataclass
class Job:
    request: ExecutionRequest
    identity: str = "local"
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    status: str = "queued"                 # queued -> running -> done | error
    result: ExecutionResult | None = None
    future: "asyncio.Future[ExecutionResult] | None" = None
    on_complete: "Callable[[Job], Awaitable[None]] | None" = None
    meta: dict = field(default_factory=dict)   # e.g. history id, abuse flags
    finished_at: float | None = None


class JobQueue:
    def __init__(self, workers: int = WORKERS, depth: int = QUEUE_DEPTH):
        self._n = workers
        self._queue: asyncio.Queue[Job] = asyncio.Queue(maxsize=depth)
        self._workers: list[asyncio.Task] = []
        self._active = 0
        self._jobs: dict[str, Job] = {}        # tracked async jobs
        self._closing = False

    async def start(self) -> None:
        self._closing = False
        self._workers = [asyncio.create_task(self._run(i)) for i in range(self._n)]

    async def stop(self, grace_s: float = SHUTDOWN_GRACE_S) -> None:
        """Graceful drain: stop accepting new work, let queued and in-flight jobs
        finish (up to grace_s), then cancel any stragglers."""
        self._closing = True
        try:
            await asyncio.wait_for(self._queue.join(), timeout=grace_s)
        except asyncio.TimeoutError:
            pass  # grace exceeded — cancel whatever is left
        for w in self._workers:
            w.cancel()
        for w in self._workers:
            try:
                await w
            except asyncio.CancelledError:
                pass
        self._workers = []

    def _enqueue(self, job: Job) -> None:
        if self._closing:
            raise QueueFull()  # draining for shutdown; reject new work
        try:
            self._queue.put_nowait(job)
        except asyncio.QueueFull:
            raise QueueFull()

    async def submit(self, request: ExecutionRequest,
                     identity: str = "local") -> ExecutionResult:
        """Synchronous: enqueue and await the result."""
        loop = asyncio.get_running_loop()
        fut: "asyncio.Future[ExecutionResult]" = loop.create_future()
        self._enqueue(Job(request=request, identity=identity, future=fut))
        return await fut

    async def submit_async(
        self, request: ExecutionRequest, identity: str,
        on_complete: "Callable[[Job], Awaitable[None]] | None" = None) -> str:
        """Non-blocking: register a tracked job and return its id."""
        self._prune()
        job = Job(request=request, identity=identity, on_complete=on_complete)
        self._enqueue(job)
        self._jobs[job.id] = job
        return job.id

    def get_job(self, job_id: str, identity: str) -> Job | None:
        self._prune()
        job = self._jobs.get(job_id)
        if job is None or job.identity != identity:
            return None
        return job

    def _prune(self) -> None:
        now = time.monotonic()
        stale = [jid for jid, j in self._jobs.items()
                 if j.finished_at is not None and now - j.finished_at > JOB_TTL_S]
        for jid in stale:
            del self._jobs[jid]

    async def _run(self, wid: int) -> None:
        while True:
            job = await self._queue.get()
            self._active += 1
            job.status = "running"
            try:
                job.result = await execute(job.request)
                job.status = "done"
                if job.future is not None and not job.future.done():
                    job.future.set_result(job.result)
            except asyncio.CancelledError:
                if job.future is not None and not job.future.done():
                    job.future.cancel()
                raise
            except Exception as exc:  # never let one job kill a worker
                job.status = "error"
                job.result = ExecutionResult(error=f"{type(exc).__name__}: {exc}")
                if job.future is not None and not job.future.done():
                    job.future.set_exception(exc)
            finally:
                job.finished_at = time.monotonic()
                self._active -= 1
                self._queue.task_done()
            if job.on_complete is not None:
                try:
                    await job.on_complete(job)
                except Exception:  # finalization must not kill the worker
                    pass

    def stats(self) -> dict:
        return {
            "workers": self._n,
            "active": self._active,
            "queued": self._queue.qsize(),
            "capacity": self._queue.maxsize,
            "tracked_jobs": len(self._jobs),
        }
