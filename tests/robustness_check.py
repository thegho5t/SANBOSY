"""Robustness checks: age-based janitor and graceful queue drain."""
import asyncio
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.executor import runner  # noqa: E402
from app.executor.jobqueue import JobQueue, QueueFull  # noqa: E402
from app.executor.runner import ExecutionRequest  # noqa: E402

results = []


def check(name, cond, detail=""):
    results.append(cond)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}{': ' + detail if detail else ''}")


def test_janitor_age():
    runner.RUNS_DIR.mkdir(parents=True, exist_ok=True)
    old = runner.RUNS_DIR / "old-orphan"
    new = runner.RUNS_DIR / "new-active"
    for d in (old, new):
        d.mkdir(exist_ok=True)
    # backdate the "orphan" to 1000s ago; leave "active" fresh
    past = time.time() - 1000
    os.utime(old, (past, past))

    removed = runner.sweep_orphans(max_age_s=300)
    check("janitor keeps fresh run dirs", new.exists(), "active run untouched")
    check("janitor removes stale run dirs", not old.exists(),
          f"removed {removed} stale")
    # full sweep (startup mode) clears everything
    runner.sweep_orphans()
    check("full sweep clears all", not new.exists())


async def test_graceful_drain():
    q = JobQueue(workers=2, depth=8)
    await q.start()
    done = {}

    async def on_complete(job):
        done[job.id] = job.status

    # a job that sleeps 3s inside the sandbox
    slow = ExecutionRequest(
        args=["/usr/bin/python3", "-I", "/src/main.py"],
        files={"main.py": "import time; time.sleep(3); print('drained ok')"},
        timeout_s=8.0)
    jid = await q.submit_async(slow, "local", on_complete)
    await asyncio.sleep(0.5)  # let a worker pick it up

    # begin graceful shutdown while the job is mid-flight
    stop_task = asyncio.create_task(q.stop(grace_s=30))
    await asyncio.sleep(0.1)  # let stop() run and set the closing flag

    # new submissions must be rejected during drain
    rejected = False
    try:
        await q.submit_async(slow, "local")
    except QueueFull:
        rejected = True
    check("rejects new work while draining", rejected)

    await stop_task
    job_done = done.get(jid) == "done"
    check("in-flight job drained (not cancelled)", job_done,
          f"final status={done.get(jid)}")


async def main():
    test_janitor_age()
    await test_graceful_drain()
    print("\n" + ("ALL ROBUSTNESS CHECKS PASS" if all(results) else "FAILURE"))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
