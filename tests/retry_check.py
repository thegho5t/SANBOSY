"""Transient-launch-failure retry: classifier correctness + retry behavior."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.executor import runner  # noqa: E402
from app.executor.runner import ExecutionResult, _is_transient_launch_failure  # noqa: E402

results = []


def check(name, cond, detail=""):
    results.append(cond)
    print(f"[{'PASS' if cond else 'FAIL'}] {name}{': ' + detail if detail else ''}")


def test_classifier():
    transient = ExecutionResult(
        stdout="", stderr="running container: creating container: cannot create "
        "sandbox: cannot read client sync file: waiting for sandbox to start: EOF",
        exit_code=128)
    check("transient launch failure detected", _is_transient_launch_failure(transient))

    user_fail = ExecutionResult(stdout="", stderr="SyntaxError: bad", exit_code=1)
    check("user error not treated as transient", not _is_transient_launch_failure(user_fail))

    user_ok = ExecutionResult(stdout="hi\n", stderr="", exit_code=0)
    check("successful run not retried", not _is_transient_launch_failure(user_ok))

    # a program that legitimately exits 128 but actually ran (has stdout)
    ran_128 = ExecutionResult(stdout="output\n",
                              stderr="cannot create sandbox", exit_code=128)
    check("128 with real output not transient", not _is_transient_launch_failure(ran_128))

    timed = ExecutionResult(timed_out=True, exit_code=None)
    check("timeout not transient", not _is_transient_launch_failure(timed))


async def test_retry_recovers():
    calls = {"n": 0}
    transient = ExecutionResult(
        stdout="", stderr="cannot read client sync file", exit_code=128)
    success = ExecutionResult(stdout="recovered\n", stderr="", exit_code=0)

    async def fake_run(cid, phase_dir, cfg, stdin, timeout, limits):
        calls["n"] += 1
        return transient if calls["n"] < 3 else success  # fail twice, then succeed

    orig = runner._run_container
    runner._run_container = fake_run
    try:
        res = await runner._run_with_retry(
            "c", runner.RUNS_DIR / "x", {}, "", 5.0, runner.DEFAULT_LIMITS)
    finally:
        runner._run_container = orig
    check("retry recovers after transient failures",
          res.exit_code == 0 and res.stdout == "recovered\n",
          f"attempts={calls['n']}")

    # exhausts retries and returns the last transient result if it never recovers
    calls["n"] = 0

    async def always_fail(cid, phase_dir, cfg, stdin, timeout, limits):
        calls["n"] += 1
        return transient

    runner._run_container = always_fail
    try:
        res = await runner._run_with_retry(
            "c", runner.RUNS_DIR / "y", {}, "", 5.0, runner.DEFAULT_LIMITS)
    finally:
        runner._run_container = orig
    check("bounded retries (1 + LAUNCH_RETRIES attempts)",
          calls["n"] == 1 + runner.LAUNCH_RETRIES, f"attempts={calls['n']}")


async def main():
    test_classifier()
    await test_retry_recovers()
    print("\n" + ("ALL RETRY CHECKS PASS" if all(results) else "FAILURE"))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
