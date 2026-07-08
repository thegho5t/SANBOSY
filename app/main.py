"""FastAPI application: REST core under /api/v1 + thin static UI at /."""
import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .api.routes import router
from .api.ratelimit import RateLimiter
from .abuse import AbuseTracker
from .executor.jobqueue import JobQueue, WORKERS
from .executor.runner import enforce_cache_limit, sweep_orphans
from . import backends, store

UI_DIR = Path(__file__).parent / "ui" / "static"
JANITOR_INTERVAL_S = float(os.environ.get("SANDBOX_JANITOR_INTERVAL_S", "60"))
JANITOR_MAX_AGE_S = float(os.environ.get("SANDBOX_JANITOR_MAX_AGE_S", "300"))


async def _janitor_loop() -> None:
    """Periodically remove run dirs orphaned by a crashed worker. Age-based, so
    active runs (fresh, self-cleaning) are never touched."""
    while True:
        await asyncio.sleep(JANITOR_INTERVAL_S)
        try:
            n = sweep_orphans(max_age_s=JANITOR_MAX_AGE_S)
            if n:
                print(f"janitor: swept {n} stale run dir(s)")
            ev = enforce_cache_limit()
            if ev:
                print(f"janitor: evicted {ev} oversized build cache(s)")
        except Exception as exc:  # a janitor hiccup must never crash the server
            print(f"janitor: sweep failed: {exc}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    n = sweep_orphans()  # startup: clear everything a prior crash left behind
    if n:
        print(f"janitor: swept {n} orphaned run dir(s) at startup")
    store.init_db()
    if backends.enabled():   # SANDBOX_REDIS_URL set -> shared state across nodes
        app.state.limiter = backends.RedisRateLimiter()
        app.state.abuse = backends.RedisAbuseTracker()
        print("backends: using Redis for rate-limit + abuse state")
    else:
        app.state.limiter = RateLimiter()
        app.state.abuse = AbuseTracker()
    app.state.queue = JobQueue()
    # streaming bypasses the queue; bound its concurrency to the same worker count
    app.state.stream_slots = asyncio.Semaphore(WORKERS)
    await app.state.queue.start()
    janitor = asyncio.create_task(_janitor_loop())
    try:
        yield
    finally:
        janitor.cancel()
        try:
            await janitor
        except asyncio.CancelledError:
            pass
        await app.state.queue.stop()  # graceful drain of in-flight jobs
        store.close_db()


app = FastAPI(title="Code Sandbox", version="1.0.0", lifespan=lifespan)
app.include_router(router, prefix="/api/v1")

if UI_DIR.exists():
    app.mount("/", StaticFiles(directory=str(UI_DIR), html=True), name="ui")
