"""Terminal: session-cap logic (unit) + a real PTY shell round-trip (integration)."""
import asyncio

import pytest

from app.executor.terminal import TerminalManager, TerminalSession


def _sess(name: str) -> TerminalSession:
    return TerminalSession(name, {}, lambda b: None)


def test_manager_enforces_per_user_and_total_caps():
    m = TerminalManager(max_total=2, per_user=1)
    assert m.rejection("alice", is_admin=False) is None
    a = _sess("alice")
    m.register(a)
    # alice already has her one terminal
    assert m.rejection("alice", is_admin=False) is not None
    # admins bypass the per-user cap...
    assert m.rejection("alice", is_admin=True) is None
    b = _sess("bob")
    m.register(b)
    # ...but the total cap binds everyone, admins included
    assert m.rejection("carol", is_admin=False) is not None
    assert m.rejection("carol", is_admin=True) is not None
    m.unregister(a)
    assert m.rejection("alice", is_admin=False) is None


def test_manager_stats_counts_active():
    m = TerminalManager(max_total=8, per_user=1)
    assert m.stats() == {"active": 0, "max": 8}
    s = _sess("x")
    m.register(s)
    assert m.stats()["active"] == 1
    m.unregister(s)
    assert m.stats()["active"] == 0


@pytest.mark.integration
@pytest.mark.flaky(reruns=2, reruns_delay=1)
async def test_terminal_session_runs_interactive_shell():
    """A real gVisor PTY session: staged file is visible, commands run, uid is
    dropped, and the session tears down cleanly when the shell exits."""
    chunks: list[bytes] = []

    async def on_out(b: bytes) -> None:
        chunks.append(b)

    s = TerminalSession("tester", {"main.py": "print('staged-ok')\n"}, on_out)
    try:
        await asyncio.wait_for(s.start(), timeout=20)
        await asyncio.sleep(0.6)  # let bash emit its first prompt
        s.write(b"echo T-$((6*7)); ls /src; id -u\n")
        await asyncio.sleep(1.0)
        s.write(b"exit\n")
        await asyncio.wait_for(s.closed.wait(), timeout=10)
    finally:
        await s.close()

    out = b"".join(chunks).decode("utf-8", "replace")
    assert "T-42" in out, out
    assert "main.py" in out, out
    assert "65534" in out, out  # non-root
