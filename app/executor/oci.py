"""Builds the per-run OCI config.json consumed by runsc.

Security-critical: every mount here is either a constant or a path we
created ourselves under the staging root. Nothing user-controlled may
ever reach a mount source.
"""
import json
from pathlib import Path

from .limits import Limits

_MASKED = [
    "/proc/kcore", "/proc/keys", "/proc/latency_stats", "/proc/timer_list",
    "/proc/timer_stats", "/proc/sched_debug", "/proc/scsi", "/sys/firmware",
]
_READONLY = ["/proc/bus", "/proc/fs", "/proc/irq", "/proc/sys", "/proc/sysrq-trigger"]

# Guest seccomp: defense-in-depth *inside* the sandbox (gVisor's Sentry is the
# primary syscall boundary against the host). Default-allow so language runtimes
# work unchanged; deny a curated set of clearly-privileged / kernel-management
# syscalls that no sandboxed user program legitimately needs. gVisor already
# emulates or blocks most of these, but a redundant guard is cheap.
_SECCOMP_DENY = [
    # tracing / other-process memory
    "ptrace", "process_vm_readv", "process_vm_writev",
    # mount / filesystem topology
    "mount", "umount", "umount2", "pivot_root", "chroot", "move_mount",
    "open_tree", "fsopen", "fsconfig", "fsmount",
    # kernel modules / boot / power
    "init_module", "finit_module", "delete_module", "create_module",
    "kexec_load", "kexec_file_load", "reboot",
    # kernel keyring
    "keyctl", "add_key", "request_key",
    # privileged / rarely-legit
    "bpf", "perf_event_open", "userfaultfd", "ioperm", "iopl",
    "swapon", "swapoff", "acct", "quotactl", "nfsservctl",
    "settimeofday", "clock_settime", "adjtimex", "clock_adjtime",
    "setns", "unshare",
]


def build_seccomp() -> dict:
    return {
        "defaultAction": "SCMP_ACT_ALLOW",
        "architectures": ["SCMP_ARCH_X86_64", "SCMP_ARCH_X86", "SCMP_ARCH_X32"],
        "syscalls": [
            {"names": sorted(_SECCOMP_DENY),
             "action": "SCMP_ACT_ERRNO", "errnoRet": 1},  # EPERM
        ],
    }


def _box_size_mb(limits: Limits) -> int:
    return max(1, limits.box_file_size_bytes // (1024 * 1024))


def build_config(args: list[str], src_dir: Path,
                 limits: Limits, rootfs: Path,
                 env: dict[str, str] | None = None,
                 uid: int = 65534, gid: int = 65534,
                 out_dir: Path | None = None, out_rw: bool = False,
                 cache_dir: Path | None = None) -> dict:
    base_env = {
        "PATH": "/usr/local/bin:/usr/bin",
        "HOME": "/box",
        "LANG": "C.UTF-8",
    }
    if env:
        base_env.update(env)
    mounts = [
        {"destination": "/proc", "type": "proc", "source": "proc"},
        {"destination": "/dev", "type": "tmpfs", "source": "tmpfs",
         "options": ["nosuid", "noexec", "mode=755", "size=4m"]},
        {"destination": "/dev/pts", "type": "devpts", "source": "devpts",
         "options": ["nosuid", "noexec", "newinstance", "ptmxmode=0666", "mode=0620"]},
        {"destination": "/dev/shm", "type": "tmpfs", "source": "shm",
         "options": ["nosuid", "noexec", "nodev", "mode=1777", "size=16m"]},
        # user source, read-only via the gofer (reads work as uid 65534).
        # nosuid/nodev/noexec: source is text — never a suid binary, device
        # node, or something to execute directly.
        {"destination": "/src", "type": "bind", "source": str(src_dir),
         "options": ["bind", "ro", "nosuid", "nodev", "noexec"]},
        # writable ephemeral workdir; tmpfs avoids the rootless-userns
        # "create file owned by unmapped uid" EINVAL and is size-capped
        {"destination": "/box", "type": "tmpfs", "source": "tmpfs",
         "options": ["nosuid", "nodev", f"size={_box_size_mb(limits)}m",
                     "mode=1777"]},
        # tmpfs is allocated on demand, so a high cap is free for languages that
        # need scratch space (e.g. Go's build cache); actual use counts against
        # the run's memory cgroup, which bounds it.
        {"destination": "/tmp", "type": "tmpfs", "source": "tmpfs",
         "options": ["nosuid", "nodev", "size=1024m"]},
    ]
    if out_dir is not None:
        # compile output shared between the compile step (rw, uid 0 so the gofer
        # can create host files) and the run step (ro, non-root exec)
        # /out holds the compiled binary and IS executed by the run step, so it
        # can't be noexec — but nosuid/nodev still apply.
        mounts.append({"destination": "/out", "type": "bind",
                       "source": str(out_dir),
                       "options": ["bind", "rw" if out_rw else "ro",
                                   "nosuid", "nodev"]})
    if cache_dir is not None:
        # persistent, content-addressed build cache (e.g. Go's GOCACHE) shared
        # across runs so compilers don't rebuild the stdlib every time. Only the
        # compile step (uid 0) mounts it, so the gofer can write through it.
        # build cache is never executed or a suid/device source
        mounts.append({"destination": "/cache", "type": "bind",
                       "source": str(cache_dir),
                       "options": ["bind", "rw", "nosuid", "nodev", "noexec"]})
    return {
        "ociVersion": "1.1.0",
        "process": {
            "terminal": False,
            "user": {"uid": uid, "gid": gid},
            "args": args,
            "env": [f"{k}={v}" for k, v in base_env.items()],
            "cwd": "/box",
            "capabilities": {
                "bounding": [], "effective": [], "inheritable": [],
                "permitted": [], "ambient": [],
            },
            "noNewPrivileges": True,
            "rlimits": [
                {"type": "RLIMIT_NOFILE", "hard": limits.nofile, "soft": limits.nofile},
                {"type": "RLIMIT_FSIZE", "hard": limits.box_file_size_bytes,
                 "soft": limits.box_file_size_bytes},
                {"type": "RLIMIT_CORE", "hard": 0, "soft": 0},
            ],
        },
        "root": {"path": str(rootfs), "readonly": True},
        "hostname": "sandbox",
        "mounts": mounts,
        "linux": {
            "namespaces": [{"type": t} for t in
                           ("pid", "network", "ipc", "uts", "mount")],
            "maskedPaths": _MASKED,
            "readonlyPaths": _READONLY,
            "seccomp": build_seccomp(),
        },
    }


def write_bundle(bundle_dir: Path, config: dict) -> None:
    # config uses an absolute root.path (shared RO skeleton), so no per-bundle
    # rootfs symlink is needed — gVisor performs mounts inside its own namespace.
    (bundle_dir / "config.json").write_text(json.dumps(config, indent=1))
