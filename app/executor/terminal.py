"""Interactive PTY-backed sandbox sessions for the web terminal.

A persistent gVisor container running an interactive shell, its pseudo-terminal
master fd bridged to async callbacks. Isolation is identical to a one-shot run —
rootless, --network=none, non-root uid 65534, a systemd cgroup scope for
memory/pids/cpu caps, a read-only rootfs, and the guest seccomp profile — with
two extra guards enforced here: a hard max lifetime and an idle timeout. The
user's editor files are staged read-only in /src, which is also the shell's cwd.

The PTY comes from runsc itself: with process.terminal=true and --console-socket,
runsc opens the pty inside the sandbox and hands us the master fd over an AF_UNIX
socket (SCM_RIGHTS). We never allocate a host tty, so nothing leaks inward.
"""
import array
import asyncio
import fcntl
import os
import shutil
import socket
import struct
import termios
import uuid
from pathlib import Path
from typing import Awaitable, Callable

from .limits import Limits, DEFAULT_LIMITS
from .oci import build_config, write_bundle
from .runner import (RUNSC, RUNSC_FLAGS, ROOTFS, RUNS_DIR,
                     _sanitize_filename, _delete, _kill)

# Interactive bash: --norc keeps it fast/predictable; -i for job control + prompt.
SHELL_ARGS = ["/usr/bin/bash", "--norc", "-i"]
SHELL_ENV = {
    "TERM": "xterm-256color",
    "HOME": "/box",
    "PS1": r"\[\e[36m\]sandbox\[\e[0m\]:\[\e[33m\]\w\[\e[0m\]$ ",
    # a couple of niceties so `ls` is readable and history works in-session
    "PAGER": "cat",
}

MAX_LIFETIME_S = float(os.environ.get("SANDBOX_TERMINAL_MAX_S", "1200"))   # 20 min
IDLE_TIMEOUT_S = float(os.environ.get("SANDBOX_TERMINAL_IDLE_S", "300"))   # 5 min
_FD_SIZE = struct.calcsize("i")


class TerminalSession:
    """One interactive shell in a fresh sandbox. `on_output` is an async callable
    invoked with raw pty bytes; call `write`/`resize` to feed it, `close` to end
    it. `closed` is set once the session has fully torn down."""

    def __init__(self, identity_name: str, files: dict[str, str],
                 on_output: Callable[[bytes], Awaitable[None]],
                 limits: Limits = DEFAULT_LIMITS,
                 on_close: Callable[["TerminalSession"], None] | None = None):
        self.id = "term-" + uuid.uuid4().hex[:12]
        self.identity_name = identity_name
        self.files = files
        self.on_output = on_output
        self.limits = limits
        self.on_close = on_close
        self.run_dir = RUNS_DIR / self.id
        self.state = self.run_dir / "state"
        self.master_fd: int | None = None
        self.proc: asyncio.subprocess.Process | None = None
        self.closed = asyncio.Event()
        self._closed = False
        self._start = 0.0
        self._last_input = 0.0
        self._outq: asyncio.Queue = asyncio.Queue()
        self._tasks: list[asyncio.Task] = []

    async def start(self) -> None:
        src = self.run_dir / "src"
        bundle = self.run_dir / "bundle"
        for d in (src, bundle, self.state):
            d.mkdir(parents=True)
        staged: set[str] = set()
        for name, content in self.files.items():
            safe = _sanitize_filename(name)
            if safe in staged:
                raise ValueError(f"duplicate staged file name: {safe!r}")
            staged.add(safe)
            (src / safe).write_bytes(content.encode("utf-8", "surrogatepass"))

        cfg = build_config(SHELL_ARGS, src, self.limits, ROOTFS,
                           env=SHELL_ENV, terminal=True, cwd="/src")
        write_bundle(bundle, cfg)

        sock_path = self.run_dir / "console.sock"
        srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        srv.bind(str(sock_path))
        srv.listen(1)
        srv.setblocking(False)

        cmd = [
            "systemd-run", "--user", "--scope", "--collect", "-q",
            "-p", f"MemoryMax={self.limits.memory_max}",
            "-p", f"MemorySwapMax={self.limits.memory_swap_max}",
            "-p", f"TasksMax={self.limits.pids_max}",
            "-p", f"CPUQuota={self.limits.cpu_quota_pct}%",
            "--",
            RUNSC, *RUNSC_FLAGS, f"--root={self.state}",
            "run", "--bundle", str(bundle),
            "--console-socket", str(sock_path), self.id,
        ]
        loop = asyncio.get_event_loop()
        self.proc = await asyncio.create_subprocess_exec(
            *cmd, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        try:
            conn, _ = await asyncio.wait_for(loop.sock_accept(srv), timeout=15)
        except (asyncio.TimeoutError, OSError) as e:
            srv.close()
            await self.close()
            raise RuntimeError(f"terminal: sandbox never opened a console ({e})")
        srv.close()
        try:
            self.master_fd = await loop.run_in_executor(None, _recv_fd, conn)
        finally:
            conn.close()
        os.set_blocking(self.master_fd, False)

        self._start = self._last_input = loop.time()
        loop.add_reader(self.master_fd, self._on_readable)
        self._tasks = [
            asyncio.create_task(self._drain_output()),
            asyncio.create_task(self._wait_proc()),
            asyncio.create_task(self._watchdog()),
        ]

    # --- pty -> client ---
    def _on_readable(self) -> None:
        try:
            data = os.read(self.master_fd, 65536)
        except (BlockingIOError, InterruptedError):
            return
        except OSError:
            data = b""
        if not data:  # EOF: the shell exited
            self._outq.put_nowait(None)
            return
        self._outq.put_nowait(data)

    async def _drain_output(self) -> None:
        while True:
            data = await self._outq.get()
            if data is None:
                await self.close()
                return
            try:
                await self.on_output(data)
            except Exception:
                await self.close()
                return

    # --- client -> pty ---
    def write(self, data: bytes) -> None:
        if self._closed or self.master_fd is None:
            return
        self._last_input = asyncio.get_event_loop().time()
        try:
            while data:
                n = os.write(self.master_fd, data)
                data = data[n:]
        except BlockingIOError:
            pass  # pty buffer full; overflow is dropped (rare for typed input)
        except OSError:
            asyncio.create_task(self.close())

    def resize(self, rows: int, cols: int) -> None:
        if self.master_fd is None:
            return
        rows = max(1, min(rows, 1000))
        cols = max(1, min(cols, 1000))
        try:
            fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ,
                        struct.pack("HHHH", rows, cols, 0, 0))
        except OSError:
            pass

    # --- lifecycle ---
    async def _wait_proc(self) -> None:
        if self.proc:
            await self.proc.wait()
        await self.close()

    async def _watchdog(self) -> None:
        loop = asyncio.get_event_loop()
        while not self._closed:
            await asyncio.sleep(2)
            now = loop.time()
            if now - self._start >= MAX_LIFETIME_S:
                await self.close(reason="max session time reached")
                return
            if now - self._last_input >= IDLE_TIMEOUT_S:
                await self.close(reason="closed after inactivity")
                return

    async def close(self, reason: str | None = None) -> None:
        if self._closed:
            return
        self._closed = True
        loop = asyncio.get_event_loop()
        if reason:
            try:
                await self.on_output(
                    f"\r\n\x1b[33m[{reason}]\x1b[0m\r\n".encode())
            except Exception:
                pass
        if self.master_fd is not None:
            try:
                loop.remove_reader(self.master_fd)
            except Exception:
                pass
            try:
                os.close(self.master_fd)
            except OSError:
                pass
            self.master_fd = None
        try:
            await loop.run_in_executor(None, _kill, self.state, self.id)
            await loop.run_in_executor(None, _delete, self.state, self.id)
        except Exception:
            pass
        if self.proc and self.proc.returncode is None:
            try:
                self.proc.kill()
            except ProcessLookupError:
                pass
        for t in self._tasks:
            if t is not asyncio.current_task():
                t.cancel()
        shutil.rmtree(self.run_dir, ignore_errors=True)
        self.closed.set()
        if self.on_close:
            try:
                self.on_close(self)
            except Exception:
                pass


def _recv_fd(conn: socket.socket) -> int:
    """Block until runsc sends the pty master fd over the console socket (a 1-byte
    message carrying an SCM_RIGHTS ancillary fd). Returns the received fd."""
    conn.setblocking(True)
    conn.settimeout(15)
    msg, ancdata, _flags, _addr = conn.recvmsg(1, socket.CMSG_LEN(_FD_SIZE))
    for level, ctype, cdata in ancdata:
        if level == socket.SOL_SOCKET and ctype == socket.SCM_RIGHTS:
            fds = array.array("i")
            fds.frombytes(cdata[: len(cdata) - (len(cdata) % _FD_SIZE)])
            if fds:
                return fds[0]
    raise RuntimeError("terminal: no console fd in handshake")


class TerminalManager:
    """Tracks live terminal sessions and enforces concurrency caps."""

    def __init__(self, max_total: int, per_user: int):
        self.sessions: dict[str, TerminalSession] = {}
        self.by_identity: dict[str, int] = {}
        self.max_total = max_total
        self.per_user = per_user

    def rejection(self, identity_name: str, is_admin: bool) -> str | None:
        """Return a reason string if this identity may not open a terminal now."""
        if len(self.sessions) >= self.max_total:
            return "the server is at its terminal limit; try again shortly"
        if not is_admin and self.by_identity.get(identity_name, 0) >= self.per_user:
            return "you already have a terminal open (one per user)"
        return None

    def register(self, session: TerminalSession) -> None:
        self.sessions[session.id] = session
        self.by_identity[session.identity_name] = \
            self.by_identity.get(session.identity_name, 0) + 1

    def unregister(self, session: TerminalSession) -> None:
        self.sessions.pop(session.id, None)
        n = self.by_identity.get(session.identity_name, 0) - 1
        if n <= 0:
            self.by_identity.pop(session.identity_name, None)
        else:
            self.by_identity[session.identity_name] = n

    def stats(self) -> dict:
        return {"active": len(self.sessions), "max": self.max_total}

    async def shutdown(self) -> None:
        for s in list(self.sessions.values()):
            await s.close()
