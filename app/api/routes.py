"""REST routes. Thin: validate -> build ExecutionRequest -> execute -> map out.

Phase 2 attach points are marked; none of them require changing this contract.
"""
import asyncio
import json
from dataclasses import replace

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from .auth import Identity, auth_enabled, require_api_key
from .ratelimit import RateLimited
from ..executor.jobqueue import QueueFull
from ..executor.limits import DEFAULT_LIMITS
from ..executor.runner import ExecutionRequest, execute_stream
from ..languages.registry import get_language, list_languages, resolve
from .. import abuse as abuse_mod
from .. import store
from .schemas import (ExecuteRequest, ExecuteResponse, JobStatusResponse,
                      JobSubmitResponse, LanguageInfo, LanguagesResponse,
                      RunDetail, RunListResponse, RunSummary, default_limits_dict)

router = APIRouter()


def _prepare(req: ExecuteRequest, request: Request, identity: Identity):
    """Validate a request, enforce quarantine/size caps, and build the
    ExecutionRequest. Shared by the sync and async submission paths. Raises
    HTTPException on any rejection."""
    try:
        lang = get_language(req.language)
    except KeyError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if sum(len(f.content) for f in req.files) > DEFAULT_LIMITS.source_cap_bytes:
        raise HTTPException(status_code=413, detail="source too large")

    if request.app.state.abuse.is_quarantined(identity.name):
        raise HTTPException(status_code=403,
                            detail="identity quarantined for abusive activity")

    files = {f.name: f.content for f in req.files}
    if req.entrypoint:
        # run whichever file the caller chose (e.g. the tab the user is viewing)
        if req.entrypoint not in files:
            raise HTTPException(
                status_code=400,
                detail=f"entrypoint '{req.entrypoint}' is not one of the files")
        ep = req.entrypoint
    elif lang.main_file in files:
        ep = lang.main_file
    elif len(files) == 1:  # single-file convenience: lone file is the entrypoint
        files = {lang.main_file: next(iter(files.values()))}
        ep = lang.main_file
    else:
        raise HTTPException(status_code=400,
                            detail=f"missing entrypoint file '{lang.main_file}'")

    timeout = None
    base = DEFAULT_LIMITS
    if req.run_timeout_ms:
        timeout = req.run_timeout_ms / 1000
        base = replace(DEFAULT_LIMITS, wall_timeout_s=timeout)
    p = resolve(lang, base, entrypoint=ep)

    exec_req = ExecutionRequest(
        args=p.run_args, files=files, stdin=req.stdin,
        limits=p.run_limits, timeout_s=timeout,
        compile_args=p.compile_args, compile_limits=p.compile_limits,
        env=p.run_env or None, compile_env=p.compile_env or None,
        compile_cache=p.compile_cache, identity=identity.name)
    return exec_req, files


async def _finalize(app, identity_name: str, req: ExecuteRequest,
                    files: dict, result) -> tuple[str | None, object]:
    """Post-run bookkeeping shared by both paths: abuse scoring + persistence."""
    verdict = abuse_mod.classify(result.as_dict())
    app.state.abuse.record(identity_name, verdict)
    rec = store.RunRecord(
        identity=identity_name, language=req.language, files=files,
        stdin=req.stdin, run_timeout_ms=req.run_timeout_ms,
        result=result.as_dict(),
        suspicious=verdict.suspicious, flags=verdict.flags)
    await store.save(rec)
    return (rec.id if store.PERSIST_ENABLED else None), verdict


@router.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


@router.get("/stats")
async def stats(request: Request) -> dict:
    return {
        "queue": request.app.state.queue.stats(),
        "rate_limit": request.app.state.limiter.stats(),
    }


@router.get("/languages", response_model=LanguagesResponse)
async def languages() -> LanguagesResponse:
    return LanguagesResponse(
        languages=[LanguageInfo(name=n, main_file=get_language(n).main_file)
                   for n in list_languages()],
        defaults=default_limits_dict(),
        auth_required=auth_enabled(),
    )


@router.post("/execute", response_model=ExecuteResponse)
async def execute_code(
    req: ExecuteRequest,
    request: Request,
    identity: Identity = Depends(require_api_key),
) -> ExecuteResponse:
    exec_req, files = _prepare(req, request, identity)
    limiter = request.app.state.limiter
    try:
        limiter.acquire(identity.name)
    except RateLimited as e:
        raise HTTPException(status_code=429, detail=e.reason,
                            headers={"Retry-After": str(e.retry_after)})
    try:
        result = await request.app.state.queue.submit(exec_req, identity.name)
    except QueueFull:
        raise HTTPException(status_code=429, detail="server busy, queue full",
                            headers={"Retry-After": "2"})
    finally:
        limiter.release(identity.name)

    hist_id, _ = await _finalize(request.app, identity.name, req, files, result)
    return ExecuteResponse(**result.as_dict(), id=hist_id)


@router.post("/execute/stream")
async def execute_stream_route(
    req: ExecuteRequest,
    request: Request,
    identity: Identity = Depends(require_api_key),
) -> StreamingResponse:
    """Live output via Server-Sent Events. Each event is JSON:
    {type:"stdout"|"stderr", data} chunks, then a final {type:"done", ...result}.
    Same validation/limits as /execute; bounded by a streaming semaphore (== worker
    count) since it bypasses the job queue."""
    exec_req, files = _prepare(req, request, identity)
    app = request.app
    limiter = app.state.limiter
    try:
        limiter.acquire(identity.name)
    except RateLimited as e:
        raise HTTPException(status_code=429, detail=e.reason,
                            headers={"Retry-After": str(e.retry_after)})

    async def gen():
        acquired = False
        try:
            # bound streaming concurrency; if none free quickly, tell the client
            try:
                await asyncio.wait_for(app.state.stream_slots.acquire(), timeout=0.01)
                acquired = True
            except asyncio.TimeoutError:
                yield _sse({"type": "done", "error": "server busy", "exit_code": None,
                            "stdout": "", "stderr": "", "timed_out": False,
                            "truncated_stdout": False, "truncated_stderr": False,
                            "wall_time_ms": 0, "stage": "run"})
                return
            async for kind, payload in execute_stream(exec_req):
                if kind == "done":
                    verdict = abuse_mod.classify(payload.as_dict())
                    app.state.abuse.record(identity.name, verdict)
                    rec = store.RunRecord(
                        identity=identity.name, language=req.language, files=files,
                        stdin=req.stdin, run_timeout_ms=req.run_timeout_ms,
                        result=payload.as_dict(),
                        suspicious=verdict.suspicious, flags=verdict.flags)
                    await store.save(rec)
                    body = {"type": "done", **payload.as_dict(),
                            "id": rec.id if store.PERSIST_ENABLED else None}
                    yield _sse(body)
                else:
                    yield _sse({"type": kind, "data": payload})
        finally:
            if acquired:
                app.state.stream_slots.release()
            limiter.release(identity.name)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj)}\n\n"


@router.post("/jobs", response_model=JobSubmitResponse, status_code=202)
async def submit_job(
    req: ExecuteRequest,
    request: Request,
    identity: Identity = Depends(require_api_key),
) -> JobSubmitResponse:
    """Async submission: enqueue and return a job id immediately; poll
    GET /jobs/{id} for status and result. Same validation and per-identity
    limits as /execute; finalization (abuse + persistence) and the rate-limiter
    release run when the job completes."""
    exec_req, files = _prepare(req, request, identity)
    app = request.app
    limiter = app.state.limiter
    try:
        limiter.acquire(identity.name)
    except RateLimited as e:
        raise HTTPException(status_code=429, detail=e.reason,
                            headers={"Retry-After": str(e.retry_after)})

    async def on_complete(job) -> None:
        try:
            hist_id, verdict = await _finalize(
                app, identity.name, req, files, job.result)
            job.meta["history_id"] = hist_id
        finally:
            limiter.release(identity.name)

    try:
        job_id = await app.state.queue.submit_async(
            exec_req, identity.name, on_complete)
    except QueueFull:
        limiter.release(identity.name)
        raise HTTPException(status_code=429, detail="server busy, queue full",
                            headers={"Retry-After": "2"})
    return JobSubmitResponse(id=job_id, status="queued")


@router.get("/jobs/{job_id}", response_model=JobStatusResponse)
async def get_job(
    job_id: str,
    request: Request,
    identity: Identity = Depends(require_api_key),
) -> JobStatusResponse:
    job = request.app.state.queue.get_job(job_id, identity.name)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    result = None
    if job.status in ("done", "error") and job.result is not None:
        result = ExecuteResponse(**job.result.as_dict(),
                                 id=job.meta.get("history_id"))
    return JobStatusResponse(id=job.id, status=job.status, result=result)


@router.get("/abuse")
async def abuse_report(
    request: Request,
    identity: Identity = Depends(require_api_key),
) -> dict:
    return request.app.state.abuse.report()


@router.get("/runs", response_model=RunListResponse)
async def list_runs(
    request: Request,
    identity: Identity = Depends(require_api_key),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> RunListResponse:
    rows = await store.list_runs(identity.name, limit=limit, offset=offset)
    return RunListResponse(
        runs=[RunSummary(**r) for r in rows], limit=limit, offset=offset)


@router.get("/runs/{run_id}", response_model=RunDetail)
async def get_run(
    run_id: str,
    identity: Identity = Depends(require_api_key),
) -> RunDetail:
    row = await store.get_run(identity.name, run_id)
    if row is None:
        raise HTTPException(status_code=404, detail="run not found")
    return RunDetail(**row)


@router.delete("/runs/{run_id}", status_code=204)
async def delete_run(
    run_id: str,
    identity: Identity = Depends(require_api_key),
) -> None:
    if not await store.delete_run(identity.name, run_id):
        raise HTTPException(status_code=404, detail="run not found")
