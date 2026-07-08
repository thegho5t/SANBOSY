"""Per-run sandbox lifecycle: stage -> runsc run -> collect (capped) -> destroy.

One fresh gVisor container per execution, wrapped in a transient systemd
user scope for cgroup v2 hard caps. Never reused.
"""
import asyncio
import os
import time
import shutil
import subprocess
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .limits import Limits, DEFAULT_LIMITS
from .oci import build_config, write_bundle

RUNTIME_ROOT = Path(os.environ.get("SANDBOX_RUNTIME_ROOT",
                                   str(Path.home() / ".sandbox")))
ROOTFS = RUNTIME_ROOT / "rootfs"
ETC_DIR = RUNTIME_ROOT / "etc"
RUNS_DIR = RUNTIME_ROOT / "runs"
CACHE_DIR = RUNTIME_ROOT / "cache"

# Per-language build cache is the one writable path that isn't a size-capped
# tmpfs (it's a persistent host bind mount). Bound it so it can't grow without
# limit over many runs or be padded by a compiler driven by hostile source.
CACHE_MAX_BYTES = int(os.environ.get("SANDBOX_CACHE_MAX_MB", "4096")) * 1024 * 1024

RUNSC = shutil.which("runsc") or str(Path.home() / ".local/bin/runsc")

# Bounded retry for transient sandbox-launch failures (the sandbox never started
# — distinct from user code running and failing). Retrying is safe because in
# this state no user code executed.
LAUNCH_RETRIES = int(os.environ.get("SANDBOX_LAUNCH_RETRIES", "2"))
RETRY_BACKOFF_S = float(os.environ.get("SANDBOX_RETRY_BACKOFF_S", "0.25"))
_TRANSIENT_MARKERS = (
    "cannot read client sync file", "waiting for sandbox to start",
    "cannot create sandbox", "creating container", "error reserving",
    "failed to start transient scope", "connection timed out",
    "transport endpoint is not connected", "resource temporarily unavailable",
)
RUNSC_FLAGS = ["--rootless", "--network=none", "--platform=systrap",
               "--oci-seccomp"]  # enforce the guest seccomp profile (off by default)
if os.environ.get("SANDBOX_DEBUG"):  # opt-in gVisor debug logging + strace
    RUNSC_FLAGS += ["--debug", "--debug-log=/tmp/sandbox-runsc.log", "--strace"]

# Build caches are content-addressed (no cross-user poisoning), but a *shared*
# cache lets one identity infer via hit-timing what another compiled. Off by
# default (shared cache = fast, fine for single-operator / trusted tenants); set
# for untrusted multi-tenant to give each identity its own cache namespace.
CACHE_PER_IDENTITY = os.environ.get("SANDBOX_CACHE_PER_IDENTITY", "0") != "0"


def _safe_seg(name: str) -> str:
    """A filesystem-safe single path segment (identity names are operator-set,
    but sanitize anyway so a name can never escape the cache root)."""
    seg = "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
    return seg[:64] or "_"


@dataclass
class ExecutionResult:
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    timed_out: bool = False
    truncated_stdout: bool = False
    truncated_stderr: bool = False
    wall_time_ms: int = 0
    error: str | None = None
    stage: str = "run"          # "compile" if it failed at the compile step

    def as_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class ExecutionRequest:
    args: list[str]                       # run argv inside the sandbox
    files: dict[str, str] = field(default_factory=dict)  # name -> content, staged in /src
    stdin: str = ""
    limits: Limits = DEFAULT_LIMITS
    timeout_s: float | None = None        # override run wall_timeout_s
    compile_args: list[str] | None = None  # if set, a two-phase compile+run
    compile_limits: Limits | None = None   # looser caps for the compile step
    env: dict[str, str] | None = None          # extra env for the run step
    compile_env: dict[str, str] | None = None  # extra env for the compile step
    compile_cache: str | None = None           # persistent build-cache name
    identity: str = "local"                    # namespaces the cache when enabled


async def _read_capped(stream: asyncio.StreamReader, cap: int) -> tuple[bytes, bool]:
    buf = b""
    truncated = False
    while True:
        chunk = await stream.read(65536)
        if not chunk:
            return buf, truncated
        if len(buf) < cap:
            buf += chunk[: cap - len(buf)]
        if len(buf) >= cap and chunk:
            truncated = True  # keep draining so the child never blocks on a full pipe


def _sanitize_filename(name: str) -> str:
    """Defense-in-depth: the API already validates names, but the executor must
    never trust its caller. Collapse to a bare basename and reject anything that
    could escape the staging dir or is otherwise unusable."""
    clean = os.path.basename(name.replace("\\", "/"))
    if (not clean or clean in (".", "..") or clean.startswith(".")
            or len(clean) > 255 or any(ord(c) < 32 for c in clean)):
        raise ValueError(f"illegal file name: {name!r}")
    return clean


def _launch_cmd(state: Path, bundle: Path, cid: str, limits: Limits) -> list[str]:
    """The systemd-run (cgroup scope) + runsc invocation. Single source of truth
    so the streaming and non-streaming paths get identical isolation."""
    return [
        "systemd-run", "--user", "--scope", "--collect", "-q",
        "-p", f"MemoryMax={limits.memory_max}",
        "-p", f"MemorySwapMax={limits.memory_swap_max}",
        "-p", f"TasksMax={limits.pids_max}",
        "-p", f"CPUQuota={limits.cpu_quota_pct}%",
        "--",
        RUNSC, *RUNSC_FLAGS, f"--root={state}",
        "run", "--bundle", str(bundle), cid,
    ]


async def _run_container(cid: str, phase_dir: Path, cfg: dict, stdin: str,
                         timeout: float, limits: Limits) -> ExecutionResult:
    """Launch one fresh gVisor container in a systemd cgroup scope, collect
    capped output, enforce the wall timeout, then destroy it."""
    bundle = phase_dir / "bundle"
    state = phase_dir / "state"
    for d in (bundle, state):
        d.mkdir(parents=True)
    write_bundle(bundle, cfg)
    result = ExecutionResult()

    cmd = _launch_cmd(state, bundle, cid, limits)
    loop = asyncio.get_event_loop()
    start = loop.time()
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    if stdin:
        proc.stdin.write(stdin.encode())
    proc.stdin.close()
    readers = asyncio.gather(
        _read_capped(proc.stdout, limits.output_cap_bytes),
        _read_capped(proc.stderr, limits.output_cap_bytes))
    try:
        await asyncio.wait_for(proc.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        result.timed_out = True
        _kill(state, cid)
        proc.kill()
        await proc.wait()
    (out, out_trunc), (err, err_trunc) = await readers
    result.wall_time_ms = int((loop.time() - start) * 1000)
    result.stdout = out.decode(errors="replace")
    result.stderr = err.decode(errors="replace")
    result.truncated_stdout = out_trunc
    result.truncated_stderr = err_trunc
    result.exit_code = None if result.timed_out else proc.returncode
    _delete(state, cid)
    return result


def _is_transient_launch_failure(res: ExecutionResult) -> bool:
    """True only when the sandbox failed to start (no user code ran): empty
    stdout, runsc's launcher exit (128), and a sandbox-start error marker."""
    if res.timed_out or res.error or res.stdout or res.exit_code != 128:
        return False
    err = res.stderr.lower()
    return any(m in err for m in _TRANSIENT_MARKERS)


async def _run_with_retry(cid: str, phase_dir: Path, cfg: dict, stdin: str,
                          timeout: float, limits: Limits) -> ExecutionResult:
    """Run a container, retrying on transient launch failures with a fresh
    bundle/state each attempt and a small backoff."""
    res = await _run_container(cid, phase_dir, cfg, stdin, timeout, limits)
    for attempt in range(1, LAUNCH_RETRIES + 1):
        if not _is_transient_launch_failure(res):
            return res
        await asyncio.sleep(RETRY_BACKOFF_S * attempt)
        res = await _run_container(f"{cid}-r{attempt}",
                                   phase_dir.with_name(phase_dir.name + f"-r{attempt}"),
                                   cfg, stdin, timeout, limits)
    return res


async def execute(req: ExecutionRequest) -> ExecutionResult:
    run_id = "run-" + uuid.uuid4().hex[:12]
    run_dir = RUNS_DIR / run_id
    src = run_dir / "src"
    out = run_dir / "out"
    result = ExecutionResult()
    run_timeout = req.timeout_s or req.limits.wall_timeout_s

    try:
        for d in (src, out):
            d.mkdir(parents=True)
        staged: set[str] = set()
        for name, content in req.files.items():
            safe = _sanitize_filename(name)
            if safe in staged:  # collision after sanitization
                raise ValueError(f"duplicate staged file name: {safe!r}")
            staged.add(safe)
            # explicit UTF-8 so staging never depends on host locale; surrogate
            # or otherwise non-encodable content fails cleanly here.
            (src / safe).write_bytes(content.encode("utf-8", "surrogatepass"))

        if req.compile_args:
            # Phase 1: compile as uid 0 (maps to the host user in rootless mode,
            # so the gofer can create the binary) into the host-backed /out.
            climits = req.compile_limits or DEFAULT_LIMITS
            cache_dir = None
            if req.compile_cache:
                base = RUNTIME_ROOT / "cache"
                if CACHE_PER_IDENTITY:
                    base = base / _safe_seg(req.identity)
                cache_dir = base / req.compile_cache
                cache_dir.mkdir(parents=True, exist_ok=True)
            ccfg = build_config(req.compile_args, src, climits, ROOTFS,
                                env=req.compile_env,
                                uid=0, gid=0, out_dir=out, out_rw=True,
                                cache_dir=cache_dir)
            cres = await _run_with_retry(run_id + "-c", run_dir / "compile",
                                         ccfg, "", climits.compile_timeout_s, climits)
            if cres.timed_out or cres.exit_code != 0:
                cres.stage = "compile"
                return cres

        # Run phase: fresh container, non-root, /out mounted read-only if present.
        rcfg = build_config(req.args, src, req.limits, ROOTFS, env=req.env,
                            out_dir=out if req.compile_args else None, out_rw=False)
        result = await _run_with_retry(run_id + "-r", run_dir / "run",
                                       rcfg, req.stdin, run_timeout, req.limits)
    except Exception as exc:  # orchestration failure, not user-code failure
        result.error = f"{type(exc).__name__}: {exc}"
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)
    return result


async def _stream_container(cid: str, phase_dir: Path, cfg: dict, stdin: str,
                            timeout: float, limits: Limits):
    """Async generator: same isolation/caps as _run_container, but yields
    ('stdout'|'stderr', text) chunks live as they arrive, then ('done', result)."""
    bundle = phase_dir / "bundle"
    state = phase_dir / "state"
    for d in (bundle, state):
        d.mkdir(parents=True)
    write_bundle(bundle, cfg)

    proc = await asyncio.create_subprocess_exec(
        *_launch_cmd(state, bundle, cid, limits), stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    if stdin:
        proc.stdin.write(stdin.encode())
    proc.stdin.close()

    q: asyncio.Queue = asyncio.Queue()
    trunc = {"out": False, "err": False}

    async def pump(name, reader):
        total = 0
        while True:
            chunk = await reader.read(4096)
            if not chunk:
                break
            if total < limits.output_cap_bytes:
                send = chunk[: limits.output_cap_bytes - total]
                total += len(send)
                await q.put((name, send))
            if total >= limits.output_cap_bytes:
                trunc[name] = True  # keep draining so the child never blocks
        await q.put((name + "_eof", None))

    pumps = [asyncio.create_task(pump("out", proc.stdout)),
             asyncio.create_task(pump("err", proc.stderr))]
    loop = asyncio.get_event_loop()
    start = loop.time()
    deadline = start + timeout
    result = ExecutionResult()
    eofs = 0
    while eofs < 2:
        remaining = deadline - loop.time()
        if remaining <= 0:
            result.timed_out = True
            break
        try:
            name, payload = await asyncio.wait_for(q.get(), timeout=remaining)
        except asyncio.TimeoutError:
            result.timed_out = True
            break
        if name.endswith("_eof"):
            eofs += 1
            continue
        yield ("stdout" if name == "out" else "stderr",
               payload.decode(errors="replace"))

    for p in pumps:
        p.cancel()
    if result.timed_out:
        _kill(state, cid)
    try:
        await asyncio.wait_for(proc.wait(), timeout=2)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
    result.wall_time_ms = int((loop.time() - start) * 1000)
    result.exit_code = None if result.timed_out else proc.returncode
    result.truncated_stdout = trunc["out"]
    result.truncated_stderr = trunc["err"]
    _delete(state, cid)
    yield ("done", result)


async def execute_stream(req: ExecutionRequest):
    """Streaming variant of execute(): yields ('stdout'|'stderr', text) chunks
    and finally ('done', ExecutionResult). Compile output is not streamed — a
    compile failure yields its stderr then a done event with stage='compile'."""
    run_id = "run-" + uuid.uuid4().hex[:12]
    run_dir = RUNS_DIR / run_id
    src = run_dir / "src"
    out = run_dir / "out"
    run_timeout = req.timeout_s or req.limits.wall_timeout_s
    try:
        for d in (src, out):
            d.mkdir(parents=True)
        staged: set[str] = set()
        for name, content in req.files.items():
            safe = _sanitize_filename(name)
            if safe in staged:
                raise ValueError(f"duplicate staged file name: {safe!r}")
            staged.add(safe)
            (src / safe).write_bytes(content.encode("utf-8", "surrogatepass"))

        if req.compile_args:
            climits = req.compile_limits or DEFAULT_LIMITS
            cache_dir = None
            if req.compile_cache:
                base = RUNTIME_ROOT / "cache"
                if CACHE_PER_IDENTITY:
                    base = base / _safe_seg(req.identity)
                cache_dir = base / req.compile_cache
                cache_dir.mkdir(parents=True, exist_ok=True)
            ccfg = build_config(req.compile_args, src, climits, ROOTFS,
                                env=req.compile_env, uid=0, gid=0,
                                out_dir=out, out_rw=True, cache_dir=cache_dir)
            cres = await _run_with_retry(run_id + "-c", run_dir / "compile",
                                         ccfg, "", climits.compile_timeout_s, climits)
            if cres.timed_out or cres.exit_code != 0:
                cres.stage = "compile"
                if cres.stderr:
                    yield ("stderr", cres.stderr)
                yield ("done", cres)
                return

        rcfg = build_config(req.args, src, req.limits, ROOTFS, env=req.env,
                            out_dir=out if req.compile_args else None, out_rw=False)
        async for ev in _stream_container(run_id + "-r", run_dir / "run", rcfg,
                                          req.stdin, run_timeout, req.limits):
            yield ev
    except Exception as exc:
        yield ("done", ExecutionResult(error=f"{type(exc).__name__}: {exc}"))
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)


def _kill(state: Path, cid: str) -> None:
    subprocess.run([RUNSC, *RUNSC_FLAGS, f"--root={state}", "kill", cid, "KILL"],
                   capture_output=True, timeout=10)


def _delete(state: Path, cid: str) -> None:
    subprocess.run([RUNSC, *RUNSC_FLAGS, f"--root={state}", "delete", "--force", cid],
                   capture_output=True, timeout=10)


def _dir_size(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += (Path(root) / f).stat().st_size
            except OSError:
                pass
    return total


def enforce_cache_limit(max_bytes: int = CACHE_MAX_BYTES) -> int:
    """Bound each per-language build cache. If a cache subdir exceeds max_bytes,
    drop it wholesale — it is content-addressed and will re-warm on the next
    build. Returns the number of caches evicted."""
    if not CACHE_DIR.exists() or max_bytes <= 0:
        return 0
    evicted = 0
    for sub in CACHE_DIR.iterdir():
        if sub.is_dir() and _dir_size(sub) > max_bytes:
            shutil.rmtree(sub, ignore_errors=True)
            evicted += 1
    return evicted


def sweep_orphans(max_age_s: float | None = None) -> int:
    """Janitor: force-delete leftover containers and run dirs.

    With max_age_s=None (startup), removes everything — nothing is running yet.
    With max_age_s set (periodic, while the server is live), removes only dirs
    older than that, so active runs (which are fresh and self-clean on finish)
    are never touched. A dir outliving max_age_s is an orphan from a crashed run.
    """
    n = 0
    if not RUNS_DIR.exists():
        return 0
    now = time.time()
    for d in RUNS_DIR.iterdir():
        if max_age_s is not None:
            try:
                if now - d.stat().st_mtime < max_age_s:
                    continue
            except OSError:
                continue
        rid = d.name
        for phase, suffix in (("compile", "-c"), ("run", "-r")):
            st = d / phase / "state"
            if st.exists():
                _delete(st, rid + suffix)
        shutil.rmtree(d, ignore_errors=True)
        n += 1
    return n
