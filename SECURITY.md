# Security model & review

This sandbox executes **hostile, untrusted code**. This document states the threat
model, the isolation boundaries, and the findings from a review of the
security-critical orchestrator glue (`app/executor/oci.py`, `app/executor/runner.py`).

## Threat model
- **Adversary:** the code being executed. Assume it actively tries to escape the
  sandbox, read/modify the host, reach the network, exhaust host resources, or affect
  other runs. Even authenticated callers are treated as hostile.
- **Trusted:** the host kernel, gVisor, the language toolchains (they process hostile
  *input* but are not themselves attacker-supplied), and the orchestrator code.
- **Assets:** the host filesystem/kernel, other concurrent runs, host availability.

## Boundaries (defense in depth — containers are never the sole boundary)
1. **gVisor (`runsc`, systrap)** — user syscalls hit the Sentry (a userspace kernel),
   not the host kernel. Primary escape boundary. `--oci-seccomp` enforces a guest
   seccomp profile as a redundant inner filter.
2. **cgroups v2** (transient systemd scope) — `MemoryMax`, `MemorySwapMax=0`,
   `TasksMax` (fork-bomb cap), `CPUQuota`, plus an orchestrator wall-clock timeout.
3. **`--network=none`** — no network interface exists inside the sandbox.
4. **OCI hardening** — read-only root, all capabilities dropped, `noNewPrivileges`,
   non-root run (uid 65534), rlimits (`NOFILE`, `FSIZE`, `CORE=0`), masked/ro `/proc`
   paths, size-capped tmpfs workdirs, no `/sys`, and `nosuid`/`nodev` on every
   writable/bind mount (`noexec` too on `/src` and `/cache`, which are never executed;
   `/out` keeps exec since the run step runs the compiled binary from it).

Every run is one fresh, ephemeral sandbox, destroyed immediately after; never reused.

## Compile vs run
Compiled languages use two containers. The **compile** step runs as uid 0 (which maps
to the host user in rootless mode, so the gofer can write the binary) with `/out` and
`/cache` writable. It processes hostile *source* with a trusted compiler — a compiler
RCE would run as root **inside the sandbox**, still fully gVisor-contained, with write
access only to `/out` (per-run, deleted) and `/cache` (bounded — see below). The
**run** step executes the produced binary as non-root (uid 65534) with `/out`
read-only. Untrusted execution is never root.

## Review findings

**Confirmed sound.** Mount *sources* are all server-controlled paths (`run_id` is a
server UUID; `src`/`out`/`cache` live under `RUNTIME_ROOT`) — no user-controlled string
reaches a mount source. `args` are registry constants with `{main}` → a constant file
name; user filenames never reach argv. `env` values come from trusted `LanguageDef`s
and are passed as an OCI list (no shell). Filenames are validated at the API and
re-sanitized to a bare basename in the executor. Cleanup is layered (per-run `rmtree`
incl. retry dirs, always-`delete`, `--collect` scope GC, startup + periodic janitor).
Launch retries are gated so user code that ran is never re-executed.

**Fixed — unbounded build cache.** `/cache` was the one writable path not otherwise
size-bounded (tmpfs mounts are size-capped; `RLIMIT_FSIZE` caps any single file). It is
now bounded per language by `enforce_cache_limit()` (janitor, `SANDBOX_CACHE_MAX_MB`,
default 4096): an over-cap cache is dropped wholesale and re-warms on the next build.

**Hardened — mount flags.** The `/src`, `/out`, and `/cache` bind mounts previously
carried no `nosuid`/`nodev`/`noexec`. They now do (`/out` keeps exec, as the run step
executes the binary from it). Redundant with `noNewPrivileges` + empty capabilities,
but closes suid/device-node vectors as defense-in-depth. Verified the WSL2 kernel
accepts the flags on these binds and all languages still run.

## Adversarial validation

Beyond the happy-path containment tests, a battery of documented escape techniques
is run against the sandbox and asserted to fail — interpreted
(`tests/hostile/adversary.py`) and as a **compiled native binary making raw syscalls**
(`tests/hostile/adversary.cpp`), both wired into `tests/test_containment.py`:

- host filesystem via 4 vectors (direct, `/proc/1/root`, `/proc/self/root`, cwd
  traversal) + probing `/home`, `/mnt/c`, `/root/.ssh`, host `/etc/shadow`
- kernel memory (`/proc/kcore`, `/dev/mem`, `/dev/kmem`) and kernel fingerprinting
  (`uname` returns gVisor's `4.19.0-gvisor`, never the host `6.6-microsoft`)
- privileged syscalls: `ptrace`, `mount`, `unshare(NEWUSER/NEWNS)`, `setns`, `bpf`
- symlink-to-root escape from the writable workdir
- runc CVE-2019-5736-style `/proc/self/exe` overwrite
- `/proc/sysrq-trigger` / `/proc/sys/*` writes, cross-namespace `kill(-1)`

Every technique is contained (interpreted: 24/24; native: 8/8). This raises
confidence but is not a substitute for an independent professional audit.

## Known residual risks (accepted / future)
- **Shared cache in a multi-user deployment.** `/cache` is shared across identities by
  default. It is content-addressed (poisoning is hash-protected), but cache hit/miss
  timing is a minor side channel revealing what others compiled. For untrusted
  multi-tenant use, set `SANDBOX_CACHE_PER_IDENTITY=1` to give each identity its own
  cache namespace (identity names are sanitized to one safe path segment).
- **Host-load starvation.** Heavy host load can make legitimate runs hit the wall-clock
  timeout. Containment is unaffected; only throughput degrades.
- **Network scenarios** remain deliberately out of scope; enabling any network would be
  a separate, explicitly-gated profile.
