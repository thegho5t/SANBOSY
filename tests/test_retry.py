"""Unit tests for transient-launch-failure detection and bounded retry."""
from app.executor import runner
from app.executor.runner import (ExecutionResult, _is_transient_launch_failure,
                                  _run_with_retry, DEFAULT_LIMITS)

TRANSIENT = ExecutionResult(
    stdout="", stderr="creating container: cannot create sandbox: cannot read "
    "client sync file: waiting for sandbox to start: EOF", exit_code=128)


def test_transient_detected():
    assert _is_transient_launch_failure(TRANSIENT) is True


def test_user_error_not_transient():
    assert _is_transient_launch_failure(
        ExecutionResult(stdout="", stderr="SyntaxError", exit_code=1)) is False


def test_success_not_transient():
    assert _is_transient_launch_failure(
        ExecutionResult(stdout="ok\n", exit_code=0)) is False


def test_128_with_output_not_transient():
    # a real program that exits 128 and produced output must not be retried
    assert _is_transient_launch_failure(
        ExecutionResult(stdout="x\n", stderr="cannot create sandbox",
                        exit_code=128)) is False


def test_timeout_not_transient():
    assert _is_transient_launch_failure(
        ExecutionResult(timed_out=True, exit_code=None)) is False


async def test_retry_recovers(monkeypatch, tmp_path):
    calls = {"n": 0}
    ok = ExecutionResult(stdout="recovered\n", exit_code=0)

    async def fake(cid, phase_dir, cfg, stdin, timeout, limits):
        calls["n"] += 1
        return TRANSIENT if calls["n"] < 3 else ok

    monkeypatch.setattr(runner, "_run_container", fake)
    res = await _run_with_retry("c", tmp_path / "p", {}, "", 5.0, DEFAULT_LIMITS)
    assert res.exit_code == 0 and calls["n"] == 3


async def test_retry_is_bounded(monkeypatch, tmp_path):
    calls = {"n": 0}

    async def always_fail(cid, phase_dir, cfg, stdin, timeout, limits):
        calls["n"] += 1
        return TRANSIENT

    monkeypatch.setattr(runner, "_run_container", always_fail)
    res = await _run_with_retry("c", tmp_path / "p", {}, "", 5.0, DEFAULT_LIMITS)
    assert res is TRANSIENT
    assert calls["n"] == 1 + runner.LAUNCH_RETRIES
