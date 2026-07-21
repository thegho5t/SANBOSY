"""Integration: hostile code is contained (real gVisor sandboxes)."""
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.flaky(reruns=2, reruns_delay=1)]

HOSTILE = Path(__file__).parent / "hostile"


async def test_baseline_runs(run_code):
    r = await run_code("python", "print('hi', 6*7)")
    assert r.exit_code == 0 and "42" in r.stdout


async def test_fork_bomb_capped(run_code):
    code = ("import os\n"
            "while True:\n"
            " try: os.fork()\n"
            " except OSError: print('capped'); break")
    r = await run_code("python", code, timeout=4.0)
    assert r.exit_code != 0 or r.timed_out or "capped" in r.stdout


async def test_infinite_loop_times_out(run_code):
    r = await run_code("python", "while True: pass", timeout=2.0)
    assert r.timed_out is True


async def test_network_blocked(run_code):
    code = ("import socket\n"
            "try:\n"
            " socket.create_connection(('1.1.1.1', 80), 2); print('OPEN')\n"
            "except OSError as e: print('blocked', e.errno)")
    r = await run_code("python", code)
    assert "OPEN" not in r.stdout and "blocked" in r.stdout


async def test_filesystem_escape_blocked(run_code):
    code = ("ok=True\n"
            "for p in ['/etc/passwd', '/newfile', '/usr/x']:\n"
            " try:\n"
            "  open(p, 'w'); ok=False\n"
            " except OSError: pass\n"
            "print('contained' if ok else 'ESCAPED')\n"
            "open('/box/w','w').write('x'); print('box-ok')")
    r = await run_code("python", code)
    assert "ESCAPED" not in r.stdout and "contained" in r.stdout
    assert "box-ok" in r.stdout


async def test_seccomp_blocks_ptrace(run_code):
    code = ("import ctypes, ctypes.util\n"
            "libc = ctypes.CDLL(ctypes.util.find_library('c'), use_errno=True)\n"
            "rc = libc.ptrace(0,0,0,0)\n"
            "print('blocked' if rc == -1 and ctypes.get_errno()==1 else 'ALLOWED')")
    r = await run_code("python", code)
    assert "blocked" in r.stdout and "ALLOWED" not in r.stdout


async def test_disk_bomb_capped(run_code):
    code = ("try:\n"
            " f=open('/box/big','wb')\n"
            " [f.write(b'A'*1048576) for _ in range(2048)]\n"
            " print('WROTE-ALL')\n"
            "except OSError: print('capped')")
    r = await run_code("python", code)
    assert "WROTE-ALL" not in r.stdout and "capped" in r.stdout


async def test_output_flood_truncated(run_code):
    code = "import sys\n[sys.stdout.write('A'*1024) for _ in range(100000)]"
    r = await run_code("python", code)
    assert r.truncated_stdout is True
    assert len(r.stdout) <= 66 * 1024


async def test_adversarial_escape_battery(run_code):
    """A battery of documented escape techniques — host-fs access, kernel-memory
    leaks, kernel fingerprinting, privileged syscalls, symlink escape, the runc
    /proc/self/exe overwrite, sysrq, cross-ns kill. None may succeed."""
    r = await run_code("python", (HOSTILE / "adversary.py").read_text())
    assert "ADVERSARY DONE (escaped=0)" in r.stdout, r.stderr[:400]
    assert "ESCAPED" not in r.stdout
    assert r.stdout.count("contained:") >= 25  # the full battery ran
    # the symlink probe must actually RESOLVE (positive control), otherwise a
    # dangling link would make every downstream fs check pass for free
    assert "link resolves" in r.stdout, r.stdout
    # and the box must prove it's the sandbox root, not the host's
    assert "value='sandbox'" in r.stdout, r.stdout


async def test_native_compiled_adversary_contained(run_code):
    """A compiled binary making raw syscalls (not libc wrappers) is contained
    identically — closes the 'ships a native exploit' gap."""
    r = await run_code("cpp", (HOSTILE / "adversary.cpp").read_text())
    assert r.exit_code == 0 and "ADVERSARY DONE (escaped=0)" in r.stdout, r.stderr[:400]
    assert "ESCAPED" not in r.stdout
    # syscalls are proven refused (EPERM), not merely absent; and the symlink
    # probe proves it resolves inside the sandbox root
    assert "Operation not permitted" in r.stdout and "link resolves" in r.stdout
