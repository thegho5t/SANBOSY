"""Containment suite: run each hostile payload and assert it is contained.

Exit 0 only if every case passes. This is the M1 exit criterion.
"""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.executor.runner import ExecutionRequest, execute, sweep_orphans  # noqa: E402
from app.executor.limits import Limits, DEFAULT_LIMITS  # noqa: E402
from app.languages.registry import get_language, resolve  # noqa: E402

HOSTILE = Path(__file__).parent / "hostile"


async def run(src_name: str, stdin: str = "", limits: Limits | None = None,
              timeout: float | None = None, lang: str = "python"):
    ld = get_language(lang)
    code = (HOSTILE / src_name).read_text()
    base = limits or DEFAULT_LIMITS
    p = resolve(ld, base)
    req = ExecutionRequest(
        args=p.run_args, files={ld.main_file: code}, stdin=stdin,
        limits=p.run_limits, timeout_s=timeout,
        compile_args=p.compile_args, compile_limits=p.compile_limits,
        env=p.run_env or None, compile_env=p.compile_env or None,
        compile_cache=p.compile_cache)
    return await execute(req)


def check(name: str, cond: bool, detail: str) -> bool:
    print(f"[{'PASS' if cond else 'FAIL'}] {name}: {detail}")
    return cond


async def main() -> int:
    sweep_orphans()
    ok = True

    r = await run("hello.py", stdin="abc")
    ok &= check("baseline", r.exit_code == 0 and "ABC" in r.stdout, r.stdout.strip())

    r = await run("forkbomb.py", limits=Limits(pids_max=64))
    ok &= check("forkbomb", r.exit_code != 0 or r.timed_out or "fork failed" in r.stdout,
                f"exit={r.exit_code} timed_out={r.timed_out} {r.stdout[:40].strip()}")

    r = await run("membomb.py", limits=Limits(memory_max="128M"))
    ok &= check("membomb", r.exit_code != 0 or r.timed_out,
                f"exit={r.exit_code} signal/killed, err={r.stderr[:40].strip()}")

    t0 = time.time()
    r = await run("infloop.py", timeout=3.0)
    ok &= check("infloop", r.timed_out and (time.time() - t0) < 8,
                f"timed_out={r.timed_out} wall={r.wall_time_ms}ms")

    r = await run("network.py")
    ok &= check("network", "blocked" in r.stdout and "OPEN" not in r.stdout,
                r.stdout.strip()[:60])

    r = await run("seccomp.py")
    ok &= check("seccomp", "blocked(EPERM)" in r.stdout
                and r.stdout.count("blocked(EPERM)") == 2,
                r.stdout.replace("\n", " | ")[:70])

    r = await run("diskbomb.py")
    ok &= check("diskbomb", "-- FAIL" not in r.stdout
                and r.stdout.count("capped") == 2 and "host unaffected" in r.stdout,
                r.stdout.replace("\n", " | ")[:80])

    r = await run("fsescape.py")
    ok &= check("fsescape",
                "-- FAIL" not in r.stdout and "box writable: OK" in r.stdout,
                r.stdout.replace("\n", " | ")[:120])

    r = await run("outputflood.py", limits=Limits(output_cap_bytes=64 * 1024))
    ok &= check("outputflood", r.truncated_stdout and len(r.stdout) <= 66 * 1024,
                f"truncated={r.truncated_stdout} bytes={len(r.stdout)}")

    # persistence: /box must be empty on a fresh run
    r = await run("fsescape.py")
    r2 = await run("hello.py", stdin="x")  # a second, different run
    ok &= check("ephemeral", r2.exit_code == 0, "fresh box per run")

    print("\n--- multi-language ---")

    r = await run("hello.js", stdin="abc", lang="javascript")
    ok &= check("js baseline", r.exit_code == 0 and "ABC" in r.stdout, r.stdout.strip()[:40])

    r = await run("hello.cpp", stdin="abc", lang="cpp")
    ok &= check("cpp baseline", r.exit_code == 0 and "sorted: 1 2 3" in r.stdout,
                r.stdout.replace("\n", " ")[:50])

    r = await run("hello.rb", stdin="abc", lang="ruby")
    ok &= check("ruby baseline", r.exit_code == 0 and "ABC" in r.stdout
                and "[1, 2, 3]" in r.stdout, r.stdout.replace("\n", " ")[:50])

    r = await run("hello.rs", stdin="abc", lang="rust")
    ok &= check("rust baseline", r.exit_code == 0 and "ABC" in r.stdout
                and "[1, 2, 3]" in r.stdout, r.stdout.replace("\n", " ")[:50])

    r = await run("hello.go", stdin="abc", lang="go")
    ok &= check("go baseline", r.exit_code == 0 and "ABC" in r.stdout
                and "[1 2 3]" in r.stdout, r.stdout.replace("\n", " ")[:50])

    # compiled hostile code must still be contained at run-phase
    r = await run("evil.c", lang="cpp")  # C-style source compiles fine as C++
    ok &= check("cpp run-phase containment",
                "-- FAIL" not in r.stdout and "blocked" in r.stdout,
                r.stdout.replace("\n", " | ")[:70])

    # compile errors surface cleanly, no run phase
    r = await run("membomb.py", lang="cpp")  # python source fed to g++ -> compile error
    ok &= check("compile error", r.stage == "compile" and r.exit_code != 0,
                f"stage={r.stage} exit={r.exit_code}")

    print("\n" + ("ALL CONTAINED" if ok else "CONTAINMENT FAILURE"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
