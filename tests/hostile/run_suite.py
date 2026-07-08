"""Containment test suite: hostile payloads that must all be contained.

Run inside WSL2: python3 -m tests.hostile.run_suite
Each case states its pass criterion and the suite exits non-zero on any failure.
"""
import asyncio
import sys

from app.executor.runner import ExecutionRequest, execute, sweep_orphans
from app.languages.registry import get_language

PAYLOADS = {
    "fork_bomb": (
        "import os\n"
        "n = 0\n"
        "try:\n"
        "    while True:\n"
        "        os.fork(); n += 1\n"
        "except OSError as e:\n"
        "    print('FORK_CAPPED', n, flush=True)\n",
        lambda r: "FORK_CAPPED" in r.stdout or r.timed_out or (r.exit_code or 0) != 0,
        "pids.max caps forking; run ends cleanly",
    ),
    "memory_bomb": (
        "a = []\n"
        "while True:\n"
        "    a.append(bytearray(10 * 1024 * 1024))\n",
        lambda r: not r.timed_out and (r.exit_code or 0) != 0,
        "OOM-killed at memory.max, not a timeout",
    ),
    "infinite_loop": (
        "while True: pass\n",
        lambda r: r.timed_out,
        "killed at wall-clock timeout",
    ),
    "output_flood": (
        "while True:\n"
        "    print('A' * 8192)\n",
        lambda r: r.truncated_stdout and len(r.stdout) <= 64 * 1024,
        "stdout truncated at cap",
    ),
    "network_socket": (
        "import socket\n"
        "try:\n"
        "    s = socket.socket(); s.settimeout(2)\n"
        "    s.connect(('1.1.1.1', 80))\n"
        "    print('CONNECTED')\n"
        "except OSError as e:\n"
        "    print('NET_BLOCKED', type(e).__name__)\n",
        lambda r: "NET_BLOCKED" in r.stdout and "CONNECTED" not in r.stdout,
        "no network interface: connect fails",
    ),
    "fs_escape_write": (
        "results = []\n"
        "for p in ('/etc/passwd', '/pwned', '/usr/bin/pwned', '/proc/sys/kernel/hostname'):\n"
        "    try:\n"
        "        open(p, 'w').write('x')\n"
        "        results.append('WROTE:' + p)\n"
        "    except OSError as e:\n"
        "        results.append('DENIED:' + p)\n"
        "print(*results, sep='\\n')\n",
        lambda r: "WROTE: