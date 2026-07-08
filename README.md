# Code Sandbox (Phase 1)

Locally-hosted sandbox that executes untrusted code under strong isolation.
Every run gets one fresh, ephemeral gVisor container, destroyed immediately after.

**Status:** Phase 1 + Phase 2 complete. Runs untrusted code end-to-end through gVisor
with the hostile-code containment suite passing (17 cases), REST API + a web UI
(Ace editor, verified in-browser), guest seccomp profile, declarative per-language limits.

**Languages:** Python, JavaScript/Node, C++, Ruby, Rust, Go. Add more by copying a
toolchain closure into the rootfs via `scripts/build_rootfs.py` and adding a
`LanguageDef` (interpreted = `run_args`; compiled = `compile_args` + `run_args`,
optional per-language `compile_env` / `compile_memory` / `compile_timeout_s`).

**Phase 2 complete:** API-key authentication, a job queue, run persistence/history,
per-identity rate limiting, and abuse detection are all done and verified.

## Abuse detection
Each finished run is classified for hostile signals — `timeout` (busy-spin),
`network_probe` (connection attempts, though the sandbox has no network),
`memory_abuse` (allocation-failure markers), `orchestration` (internal errors) —
and a rolling per-identity abuse score is kept. Detection is always on and
observational; suspicious runs are marked in history (⚠ in the UI) and surfaced at
`GET /api/v1/abuse`. Auto-quarantine is **off by default** — set
`SANDBOX_ABUSE_THRESHOLD` > 0 to block an identity (403) once its score in the
window (`SANDBOX_ABUSE_WINDOW_S`, default 300s) is exceeded. In-memory per-process.

## Rate limiting
Two per-identity caps (keyed on the authenticated identity), both **off by default**
so local use is unthrottled:
- `SANDBOX_RATE_PER_MIN` — requests per rolling 60s window (0 = off)
- `SANDBOX_MAX_INFLIGHT` — simultaneous in-flight runs (0 = off)

Exceeding either returns **429** with a `Retry-After` header. Config is visible at
`GET /api/v1/stats`. In-memory per-process (a shared store like Redis is the
horizontal-scale attach point).

## Build cache (fast compiles)
Compiled languages that would otherwise rebuild their standard library every run
(Go) use a **persistent, content-addressed build cache** at `~/.sandbox/cache/<lang>`,
bind-mounted read-write into the compile step only (which runs as uid 0, so the gofer
can write it). It's shared across runs and safe to share — entries are keyed by content
hash. `setup_wsl2.sh` pre-warms Go's cache (`go build std`) so even the first sandboxed
build is fast (~3-4s vs ~30s cold). A language opts in via `LanguageDef.compile_cache`
(the cache subdir) plus pointing its env at `/cache` (e.g. `GOCACHE=/cache`).

For untrusted multi-tenant use, `SANDBOX_CACHE_PER_IDENTITY=1` gives each identity its
own cache namespace — closing a cache-hit *timing* side channel (no poisoning risk,
since entries are content-hashed) at the cost of a cold first build per identity. Off
by default. Identity names are sanitized to a single safe path segment, so a name can
never escape the cache root.

## Run history
Each run is persisted to a SQLite DB (`~/.sandbox/history.db`, stdlib `sqlite3`,
no external service) — source, stdin, and result. `POST /execute` returns the run
`id`; `GET /runs` lists summaries, `GET /runs/{id}` returns the full record
(including source, for replay), `DELETE /runs/{id}` removes one. Rows are scoped by
identity: with auth on, callers see only their own runs. The UI has a history panel
with click-to-replay and delete. Disable with `SANDBOX_PERSIST=0` (reverts to Phase
1 throwaway behaviour).

## Job queue
Requests are enqueued onto a bounded FIFO queue drained by a fixed worker pool
(`SANDBOX_WORKERS`, default 4; `SANDBOX_QUEUE_DEPTH`, default 32). When the queue is
full the API returns **429** with `Retry-After` instead of piling up unbounded
in-flight sandboxes. `GET /api/v1/stats` reports the pool state. In-process and
dependency-free.

Two submission modes over the same pool:
- **Synchronous** — `POST /execute` enqueues and awaits the result (default; ideal
  for the UI and short runs).
- **Async** — `POST /jobs` enqueues and returns `{id, status}` immediately (202);
  poll `GET /jobs/{id}` for `status` (queued → running → done/error) and the result
  when finished. Same validation, auth, rate limits, and abuse checks as `/execute`;
  finalization (history + abuse scoring) runs on completion. Completed jobs are
  retained for `SANDBOX_JOB_TTL_S` (default 300s) then pruned. Good for long runs
  where holding an HTTP connection open is undesirable.

## Authentication
Additive and backward-compatible: with **no keys configured, auth is disabled** and
the service behaves exactly as in Phase 1 (local single-operator use). Create a key
and auth turns on automatically for `POST /api/v1/execute`:
```bash
python3 scripts/make_key.py operator     # prints the raw key ONCE; stores only its hash
python3 scripts/make_key.py --list
python3 scripts/make_key.py --revoke operator
```
Clients send `X-API-Key: <key>` (the UI shows a key field when auth is on and
remembers it in localStorage). Keys are stored SHA-256-hashed in
`~/.sandbox/api_keys.json` (mode 0600); comparison is constant-time. `GET /languages`
advertises `auth_required` and stays open so the UI can bootstrap. The resolved
identity is stashed on `request.state` for the rate-limit/quota layers to key off.

Compiled languages use a **two-phase** flow: the compile step runs as uid 0
(which maps to the host user in rootless mode, so the gofer can write the binary)
into a host-backed `/out` with looser limits; the run step is a **separate fresh
container** that mounts `/out` read-only and executes the binary as non-root
(uid 65534). Compile failures return `stage: "compile"` with the compiler output.

## Security model
- **Escape prevention:** gVisor (`runsc`, `--platform=systrap`) is the syscall
  boundary — user code never touches the host kernel directly.
- **Resource containment:** cgroups v2 via a transient systemd user scope —
  `MemoryMax`, `MemorySwapMax=0`, `TasksMax` (fork-bomb cap), `CPUQuota`; plus an
  orchestrator wall-clock timeout.
- **Network:** `--network=none` — no interface exists inside the sandbox.
- **Hardening:** read-only rootfs, all capabilities dropped, `no_new_privs`,
  non-root (uid 65534), rlimits, output size cap, ephemeral tmpfs workdir, and a
  guest seccomp profile (defense-in-depth inside the sandbox) enforced via runsc
  `--oci-seccomp` — off by default in gVisor, so the flag is required.

See [PLAN.md](PLAN.md) for the full architecture and Phase 2 attach points, and
[SECURITY.md](SECURITY.md) for the threat model, isolation boundaries, and the
orchestrator security review.

## Architecture notes (WSL2-specific findings)
- Runtime lives on the ext4 side (`~/.sandbox`), never `/mnt/c`.
- **Rootless gVisor on this WSL2 kernel cannot bind-mount pre-existing top-level
  host dirs** (`/usr`, `/etc`, …) — the root mount is locked. So the toolchain is
  *copied* into a shared read-only rootfs once (`scripts/build_rootfs.py`). This is
  also where Piston-package toolchains attach in M3.
- **The sandbox uid (65534) is unmapped in the rootless userns**, so the gofer
  cannot create host files owned by it. The writable workdir `/box` is therefore a
  **tmpfs**; user source is mounted read-only at `/src`.

## Setup (run inside WSL2 Ubuntu)
```bash
sudo apt-get install -y nodejs gcc g++ ruby rustc golang-go   # host toolchains
python3 -m pip install --user fastapi "uvicorn[standard]"
bash scripts/setup_wsl2.sh                       # installs runsc, builds rootfs +
                                                 # copies every available toolchain in
```
`setup_wsl2.sh` detects `python3.12`, `node`, `gcc`, `ruby`, `rustc`, and `go`, and
copies each one's self-contained closure into the shared read-only rootfs (~1.5 GB
with all six).

## Run
```bash
# CLI (single run) — languages: python, javascript, ruby, cpp, rust, go
python3 -m app.cli python tests/hostile/hello.py --stdin "hi"
python3 -m app.cli go     tests/hostile/hello.go --stdin "hi"

# API + UI  — start from a LOGIN shell (bash -lic) so the server inherits the
# systemd user session; otherwise systemd-run --user hangs and runs time out.
bash -lic 'exec python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000'
#   POST /api/v1/execute   GET /api/v1/languages   GET /api/v1/healthz
#   UI at http://127.0.0.1:8000/  (reachable from Windows via WSL2 localhost forwarding)
```
Common ops are wrapped in a `Makefile` (`make help`; install with `sudo apt install
make`): `setup`, `test`, `test-unit`, `run`, `service-install`, `clean-cache`, …

## Streaming output
`POST /api/v1/execute/stream` returns **Server-Sent Events** — `{type:"stdout"|"stderr",
data}` chunks delivered live as the program prints, then a final `{type:"done", …result,
id}`. Same validation, limits, persistence, and abuse scoring as `/execute`. It bypasses
the job queue (a streamed connection is long-lived), so it's bounded by its own
semaphore sized to the worker count. The UI has a **stream** toggle. Compile output
isn't streamed; a compile error emits its stderr then a done event with `stage:compile`.

## Horizontal scale (optional Redis)
The rate limiter and abuse tracker are per-process by default. Set `SANDBOX_REDIS_URL`
(e.g. `redis://host:6379`) and both move to Redis so per-identity caps and abuse scores
are shared across nodes — the rate check is a single atomic Lua script, so concurrent
nodes can't jointly exceed a cap. The job queue stays per-node (a load balancer
distributes; each node runs its own workers); run history is a SQLite file you can point
at shared storage. Unset the var and it's back to the dependency-free in-memory
backends. `GET /stats` reports which backend is active. Install with `pip install
'.[redis]'`.

## Multi-file programs
`POST /execute` accepts multiple files; all are staged side-by-side in `/src`, so the
entrypoint can pull in siblings (Python `import helper`, C++ `#include "helper.h"`,
Ruby `require_relative`, JS `require('./helper')`, and Go — the whole `/src` package
via `go build -C /src .`). One file must be named the language's entrypoint
(`GET /languages` exposes each `main_file`, e.g. `main.py`); a lone file is auto-treated
as the entrypoint. The UI has file tabs (add / rename / delete) and download/upload of
a whole project as JSON. Python runs with `-E -s` (not `-I`) so `/src` stays on
`sys.path` for sibling imports — full isolation still comes from the sandbox, not the
interpreter flag.

## Deployment
Run as a **systemd user service** (deliberately *user*, not system — the executor
needs `systemd-run --user` for per-run cgroup scopes and rootless runsc):
```bash
make service-install          # copies deploy/sandbox.service, enables + starts it
loginctl enable-linger "$USER"  # keep it running without an active login session
```
Tunables live in `~/.config/sandbox.env` (template: `deploy/sandbox.env.example`).
The unit uses `SIGTERM` + `TimeoutStopSec=45` so restarts drain in-flight jobs.

**CI:** `.github/workflows/ci.yml` runs the 53 unit tests on Python 3.11/3.12 for
every push/PR (no gVisor needed). Integration tests run locally under WSL2
(`make test-integration`).

## Input validation & limits
Requests are validated at the API boundary and again (defense-in-depth) in the
executor, so malformed or abusive input is rejected cleanly rather than causing an
unhandled error:
- **File names** must be bare names — no path separators, `.`/`..`, or control
  characters (blocks traversal); ≤255 chars. Names must be unique.
- **File count** ≤16; **total source** ≤256 KB (413); **stdin** ≤256 KB (422).
- Source is written as explicit UTF-8, so staging never depends on host locale.
- Output (stdout/stderr) is capped at 64 KB each and drained past the cap so a
  flooding program can't block or exhaust orchestrator memory.
- Unknown language → 400; validation failures → 422; oversized source → 413.

## Operational robustness
- **Transient-launch retry** — if `runsc`/`systemd-run` fails to *start* the sandbox
  (a transient infra hiccup under load), the launch is retried up to
  `SANDBOX_LAUNCH_RETRIES` (default 2) with backoff. Retries are gated on a strict
  signature (empty stdout + runsc's 128 exit + a sandbox-start error marker) so user
  code that ran is never re-executed.
- **Graceful shutdown** — on SIGTERM the queue stops accepting new work (new
  requests → 429) and drains in-flight jobs, up to `SANDBOX_SHUTDOWN_GRACE_S`
  (default 35s), before cancelling stragglers. Runs aren't killed mid-execution by a
  restart.
- **Janitor** — every run cleans up its own dir on completion. A startup sweep clears
  anything a prior crash left behind, and a periodic sweep
  (`SANDBOX_JANITOR_INTERVAL_S`, default 60s) removes dirs older than
  `SANDBOX_JANITOR_MAX_AGE_S` (default 300s) — age-based, so active runs are never
  touched.

## Known limitations
- Under heavy host load (e.g. many concurrent sandboxes, or an unrelated CPU/memory
  hog like a browser), legitimate runs can hit the wall-clock timeout — the Sentry and
  compile/run steps get starved. Containment is unaffected; only throughput degrades.
- Go still gets a larger compile memory cap (2 GB) and a 30s compile timeout because
  its compiler is heavier than the others; with the warm build cache (below) real
  compiles are ~3-4s, on par with Rust and C++.

## Tests
The primary harness is **pytest** — 53 fast unit tests (validation, auth, rate limit,
abuse, store, retry classifier, job queue) plus 24 integration tests (containment,
every language, REST API) that launch real gVisor sandboxes.
```bash
pip install -e '.[test]'                 # pytest, pytest-asyncio, httpx
pytest                                   # everything (needs runsc for integration)
pytest -m "not integration"              # 53 unit tests only — fast, no gVisor (CI)
pytest -m integration                    # the sandboxed end-to-end tests
```
Integration tests auto-skip when `runsc` isn't installed, so the unit suite runs
anywhere. The older standalone `tests/*_check.py` / `*.sh` scripts remain as manual
end-to-end probes (concurrency, stress, malformed-input fuzzing, per-feature flows).

## Layout
```
app/
  main.py              FastAPI app + static UI mount + startup janitor
  api/{routes,schemas} REST core; schemas.py is the frozen v1 contract
  api/auth.py          API-key auth (Phase 2); identity → request.state
  executor/jobqueue.py bounded queue + worker pool (Phase 2); backpressure → 429
  store.py             SQLite run history (Phase 2); identity-scoped
  api/ratelimit.py     per-identity rate + concurrency caps (Phase 2)
  abuse.py             heuristic abuse scoring + quarantine (Phase 2)
  executor/
    runner.py          per-run lifecycle: stage -> runsc -> capped collect -> destroy
    oci.py             per-run OCI config (mounts, caps, rlimits) — mount sources
                       are constants/our-dirs only, never user-controlled
    cgroups via systemd-run transient scope inside runner.py
    limits.py          all tunables in one dataclass
  languages/registry.py  language -> entrypoint + argv template
  ui/static/index.html   web UI: Ace editor (syntax highlight, 6 templates,
                         multi-file tabs, ⌘/Ctrl+Enter), light/dark theme,
                         output/status chips, history replay, live queue stats,
                         resizable split, stream + async run toggles, shareable
                         permalinks, project download/upload; textarea fallback offline
  backends.py            optional Redis-backed rate-limit + abuse (horizontal scale)
scripts/
  setup_wsl2.sh, build_rootfs.py
tests/
  run_hostile.py, concurrency.sh, hostile/*.py
```
