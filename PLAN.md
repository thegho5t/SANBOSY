# Implementation Plan — Local Multi-Language Code Sandbox (Phase 1)

Stack: **Python + FastAPI/uvicorn** (I/O-bound orchestration, free OpenAPI for the
API-first mandate; rejected Go — better at low-level work but slower to audit/iterate
for a single-operator local service). The API contract in `app/api/schemas.py` is the
stable seam so a Go executor rewrite in Phase 2 wouldn't touch clients.

Engine: **Piston packages, not Piston runtime.** Piston runs one always-on shared
container, which breaks "fresh ephemeral sandbox per run" and "gVisor per execution".
We keep Piston's prebuilt toolchains (copied into our read-only rootfs) and drive one
per-run `runsc` container ourselves. (Approved deviation.)

## Request lifecycle
`POST /api/v1/execute` → validate (language whitelist, source cap) → stage source into
a fresh run dir → build per-run OCI config → launch `runsc` inside a transient systemd
cgroup scope → enforce wall-timeout + capped stdout/stderr → `runsc delete` + shred run
dir. Nothing persists; a startup janitor sweeps orphans from prior crashes.

## Isolation (as built)
- gVisor `--platform=systrap --network=none --rootless`.
- cgroups v2 via `systemd-run --user --scope`: `MemoryMax`, `MemorySwapMax=0`,
  `TasksMax`, `CPUQuota`.
- OCI: read-only rootfs, all caps dropped, `noNewPrivileges`, uid/gid 65534,
  rlimits (NOFILE/FSIZE/CORE), masked/readonly `/proc` paths.
- Writable workdir `/box` is tmpfs (size-capped); source read-only at `/src`.
- gVisor's Sentry is the primary syscall filter; a coarse guest seccomp profile is
  planned as defense-in-depth (M5), not per-language minimization.

## Milestones
- **M0 environment** — DONE (runsc installed, systrap verified, rootfs builder).
- **M1 Python PoC + hostile suite** — DONE (all 9 containment cases pass).
- **M2 REST API + thin UI** — DONE (endpoints verified, containment holds over HTTP,
  concurrency stress clean).
- **M3 multi-language** — DONE. Node (binary + `/usr/share/nodejs` runtime data), then
  C and C++ (two-phase compile/run in two fresh containers, compile as uid 0 → `/out`,
  run non-root from `/out` read-only). All four languages pass the containment suite,
  including compiled hostile code contained at the run phase and clean `stage:compile`
  errors. Toolchains copied into the rootfs via `build_rootfs.py` (~1.2 GB total).
  Note: we install toolchains from apt and copy their closures rather than fetching
  Piston packages — since the WSL2 finding forces a copy-into-rootfs step regardless,
  the Piston-package indirection added no value here.
- **M5 hardening** — DONE. Guest OCI seccomp profile (default-allow, denies a curated
  set of privileged/kernel-management syscalls: ptrace, mount family, module load,
  keyring, bpf, userfaultfd, setns/unshare, …) enforced via runsc `--oci-seccomp`
  (off by default — this was the key finding). Per-language limits made declarative on
  `LanguageDef` (`run_memory`, `compile_memory`) and applied through one `resolve()`
  helper shared by the CLI and API, removing the duplicated hardcoded compile cap.
  Suite extended with a seccomp case (ptrace/unshare → EPERM); all 14 cases pass.
- **M4 UI** — DONE. Verified in-browser: page renders, all four languages selectable,
  Python and C++ (two-phase) both run end-to-end from the UI with correct output/exit
  status. WSL2 localhost forwarding makes the WSL-side server reachable from Windows.
  **Launch gotcha:** the server must be started from a *login* shell (`bash -lic`) so
  it inherits `XDG_RUNTIME_DIR` and the systemd user session — otherwise
  `systemd-run --user --scope` (the cgroup mechanism) hangs and every run times out.
  `.claude/launch.json` does this.

## Deviations discovered during build (WSL2 kernel)
1. Rootless gVisor can't bind-mount locked top-level host dirs → copy toolchain into a
   shared RO rootfs instead of bind-mounting `/usr`.
2. Sandbox uid is unmapped in the rootless userns → gofer can't create host files →
   `/box` is tmpfs, source is a read-only `/src` mount.

## Phase 2 progress
Scope trimmed to **three languages** (Python, JavaScript, C++) at the operator's
request; the C entry was removed (toolchain remains in the rootfs, just not exposed).

- **Auth = DONE.** `app/api/auth.py`: API-key dependency on `POST /execute`. Disabled
  when no keys exist (Phase 1 local default preserved), auto-enabled once a key is
  created via `scripts/make_key.py`. Keys stored SHA-256-hashed (0600), constant-time
  compare, identity stashed on `request.state.identity` for downstream layers. Verified
  end-to-end (401 without/with bad key, 200 with valid key) and in-browser.

- **Job queue = DONE.** `app/executor/jobqueue.py`: bounded FIFO queue drained by a
  fixed worker pool (`SANDBOX_WORKERS`/`SANDBOX_QUEUE_DEPTH`), replacing the semaphore.
  REST stays synchronous (handler awaits its job); a full queue returns HTTP 429 with
  `Retry-After`. `GET /stats` exposes pool state. Verified: 12 slow jobs at a
  2-worker/2-depth pool → 4×200, 8×429. In-process, dependency-free.

- **Persistence/history = DONE.** `app/store.py`: SQLite (stdlib, self-hostable) stores
  each run (source, stdin, result). `POST /execute` returns the run `id`; `GET /runs`,
  `GET /runs/{id}`, `DELETE /runs/{id}` — all identity-scoped. UI history panel with
  click-to-replay + delete. Toggle with `SANDBOX_PERSIST=0`. Verified end-to-end.

- **Rate limiting = DONE.** `app/api/ratelimit.py`: per-identity request-rate (sliding
  60s window, `SANDBOX_RATE_PER_MIN`) and concurrency (`SANDBOX_MAX_INFLIGHT`) caps,
  acquire-before-submit / release-in-finally around the queue. Either cap off by
  default. 429 + `Retry-After` on exceed; config in `GET /stats`. Verified: 10 reqs at
  a 5/min cap → 5×200 then 5×429.

- **Abuse detection = DONE.** `app/abuse.py`: heuristic per-run classifier (timeout,
  network_probe, memory_abuse, orchestration) feeding a rolling per-identity score.
  Suspicious runs marked in history (⚠ in UI) and surfaced at `GET /abuse`. Quarantine
  (403) off by default; enabled via `SANDBOX_ABUSE_THRESHOLD`. Verified: probe+timeout
  → score 5 → quarantined; default threshold never blocks; clean runs not flagged.

**Phase 2 is functionally complete.**

- **Async submit/poll = DONE.** `POST /jobs` (202 + job id) and `GET /jobs/{id}`
  (status queued→running→done/error + result) layered over the same worker pool via
  `submit_async`/`get_job` and an `on_complete` hook that runs finalization (abuse +
  persistence) and releases the rate-limiter slot. Completed jobs pruned after
  `SANDBOX_JOB_TTL_S`. Sync `/execute` unchanged. History store now uses WAL +
  busy_timeout for concurrency robustness. Verified end-to-end.

- **More languages = DONE (6 total).** Added Ruby (interpreted), Rust and Go (compiled)
  alongside Python, JavaScript, C++. Go required env plumbing (`LanguageDef.compile_env`
  / `run_env` → `ExecutionRequest` → `build_config`), a per-language compile-timeout
  override, a larger `/tmp` for its build cache, and a 2 GB compile cap. All six pass
  the containment suite. Adding another language is now: install toolchain → copy its
  closure with `build_rootfs.py` → add a `LanguageDef`.

- **Fast compiles = DONE.** Persistent content-addressed build cache bind-mounted rw
  into the compile step (uid 0) at `/cache`, via `LanguageDef.compile_cache` →
  `ExecutionRequest.compile_cache` → a `~/.sandbox/cache/<name>` mount in `oci.py`.
  `setup_wsl2.sh` pre-warms Go's cache with `go build std`. Result: Go compile dropped
  from ~13s/run (and a ~31s cold build) to ~3-4s, on par with Rust/C++. Safe to share
  across runs/identities because Go's cache is content-hash-keyed.

- **Edge-case & robustness hardening = DONE.** API + executor input validation (bare
  unique file names, ≤16 files, ≤256 KB source/stdin, UTF-8 staging), rate-limit/abuse
  map GC (no per-identity leak), graceful shutdown (drain in-flight jobs on SIGTERM),
  a periodic age-based janitor (removes crashed-worker orphans without touching active
  runs), bounded retry on transient sandbox-launch failures (gated so user code never
  re-runs), and verified per-run disk containment (RLIMIT_FSIZE + tmpfs caps → a disk
  bomb hits EFBIG, host untouched). Verified with `tests/edge_check.sh`, `edge_big.sh`,
  `robustness_check.py`, `shutdown_check.sh`, `retry_check.py`, and the containment suite
  (17 cases incl. diskbomb). Concurrent-load stress (`stress_check.sh`: 10 benign + 30
  hostile jobs at once → no cross-run leakage, no fd/dir/proc leak) and API malformed-
  input fuzzing (`malformed_check.sh`: 15 bad inputs → clean 4xx, never 5xx; traversal /
  control-char / oversized all rejected at the validation layer) both pass.

- **Pytest suite = DONE.** `pyproject.toml` `[tool.pytest.ini_options]` (asyncio auto,
  `integration` marker, unit/integration split). 53 unit tests (validation, auth,
  ratelimit, abuse, store, retry classifier, jobqueue — no gVisor, ~11s) + 24
  integration tests (containment, all languages, REST API via TestClient — real
  sandboxes). Integration auto-skips when `runsc` is absent, so `pytest -m "not
  integration"` is CI-runnable anywhere. 77 pass. Standalone `*_check.py`/`*.sh` scripts
  retained as manual e2e probes.

- **Security review = DONE.** Audited the orchestrator glue (`oci.py`, `runner.py`)
  against the threat model — see [SECURITY.md](SECURITY.md). Confirmed mount sources /
  argv / env are never user-controlled, cleanup is layered, retries can't double-run
  code. One finding fixed: the persistent build `/cache` had no size cap (every other
  writable path is bounded) → `enforce_cache_limit()` in the janitor
  (`SANDBOX_CACHE_MAX_MB`, default 4 GB). Residual multi-user cache-sharing side channel
  documented for the public phase.

- **Deployment + CI + cache-isolation = DONE.** systemd *user* service
  (`deploy/sandbox.service`, drains on SIGTERM), env template, and a `Makefile` for
  setup/test/run/service ops. GitHub Actions (`.github/workflows/ci.yml`) runs the unit
  suite on Python 3.11/3.12 per push. Closed the security-review finding: build caches
  can be isolated per identity (`SANDBOX_CACHE_PER_IDENTITY=1`), identity names
  sanitized to one safe path segment (`_safe_seg`, unit-tested) so they can't escape the
  cache root. 85 tests pass.

- **Web UI overhaul + multi-file = DONE.** Ace editor (syntax highlight, per-language
  templates, ⌘/Ctrl+Enter), status chips, history replay, live queue stats, resizable
  split, async-run toggle (via `/jobs`), shareable permalinks (state in URL hash), and
  **multi-file tabs** (add/rename/delete). Backend: `/languages` now exposes each
  `main_file`; Python switched from `-I` to `-E -s` so sibling imports resolve from
  `/src` (containment unchanged — verified). Multi-file Go works via `go build -C /src .`
  (whole-package build). Project download/upload as JSON. UI degrades to a plain textarea
  if the Ace CDN is blocked. 88 tests pass (multi-file Python + Go + main_file coverage).

- **Theme + streaming + Redis = DONE.** Light/dark theme toggle (persisted, Ace theme
  follows). SSE streaming (`POST /execute/stream`): live stdout/stderr chunks then a done
  event, same isolation as `/execute` (shared `_launch_cmd`), bounded by a stream
  semaphore, UI stream toggle. Optional Redis backend (`app/backends.py`, gated on
  `SANDBOX_REDIS_URL`) for rate-limit + abuse shared across nodes — atomic Lua rate
  check; in-memory default unchanged. 95 tests pass (added streaming + Redis coverage);
  CI runs a Redis service so backend tests execute there too.

Remaining growth (all non-blocking, no contract change): even more languages; sharing
the job queue / history across nodes (Redis Streams / Postgres). Network scenarios would
require relaxing `--network=none` behind a separate, explicitly-gated profile.
