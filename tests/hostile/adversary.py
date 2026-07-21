"""Adversarial escape battery. Every probe prints EVIDENCE, not just a verdict:

    contained: <name>  [<why>]
    ESCAPED:   <name>  [<why>]

`<why>` distinguishes the two ways a probe can pass, which a bare boolean hides:
  * blocked(EPERM/EACCES) — gVisor actively refused the syscall. Strong.
  * absent(ENOENT/ENOSYS) — the target/feature isn't present. Weak on its own,
    so we pair those with a POSITIVE DISCRIMINATOR: the sandbox's UTS hostname is
    "sandbox" and its kernel/uid are gVisor's. If any vector ever surfaced the
    host's WSL hostname, 6.6-microsoft kernel, or the 'mohitkoul' account, the
    root would be the real one — that's the actual escape signal, independent of
    whether a given decoy file happens to exist.

A single ESCAPED line is a real containment failure.
"""
import ctypes
import ctypes.util
import errno
import os

libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)

# Anything that would only appear if we were looking at the real host.
HOST_MARKERS = ("mohitkoul", "microsoft", "6.6.", "/mnt/c", "WSL")
BLOCKED = {errno.EPERM, errno.EACCES}
ABSENT = {errno.ENOENT, errno.ENOSYS, errno.EINVAL, errno.ENODEV, errno.ENXIO,
          errno.EOPNOTSUPP}

_escapes = 0


def why(e: int) -> str:
    name = errno.errorcode.get(e, str(e))
    tag = "blocked" if e in BLOCKED else "absent" if e in ABSENT else "errno"
    return f"{tag}({name})"


def verdict(name: str, escaped: bool, detail: str = "") -> None:
    global _escapes
    _escapes += bool(escaped)
    tail = f"  [{detail}]" if detail else ""
    print(f"{'ESCAPED' if escaped else 'contained'}: {name}{tail}")


def probe_read(name: str, path: str) -> bool:
    """Read a path; ESCAPED only if it yields real host content. Always logs how
    many bytes came back (or the errno) so 'nothing to read' is visible, not
    silently equated with 'blocked'."""
    try:
        data = open(path, "rb").read(8192)
    except OSError as e:
        verdict(name, False, f"{why(e.errno)} at open")
        return False
    hits = [m for m in HOST_MARKERS if m.encode() in data]
    verdict(name, bool(hits),
            f"read {len(data)}B, host-markers={hits or 'none'}")
    return bool(hits)


def read_bytes(path: str) -> bytes:
    try:
        return open(path, "rb").read(8192)
    except OSError:
        return b""


def probe_syscall(name: str, rc: int, escaped_when_zero: bool = True) -> None:
    """Log a raw syscall's rc AND errno so a blocked(EPERM) pass is never
    confused with an absent(ENOSYS) one."""
    e = ctypes.get_errno()
    escaped = (rc == 0) if escaped_when_zero else (rc >= 0)
    verdict(name, escaped, f"rc={rc} {why(e)}")


print("=== host filesystem: no vector may return host content ===")
probe_read("read /etc/passwd", "/etc/passwd")
probe_read("/proc/1/root/etc/passwd", "/proc/1/root/etc/passwd")
probe_read("/proc/self/root/etc/passwd", "/proc/self/root/etc/passwd")
probe_read("/proc/self/cwd traversal", "/proc/self/cwd/../../../etc/passwd")
probe_read("encoded traversal (readlink)", "/proc/self/root/../../etc/passwd")
for p in ("/home/mohitkoul", "/mnt/c", "/root/.ssh/id_rsa", "/etc/shadow"):
    # existence AND content: a bare exists() can't tell a host file from a decoy
    if os.path.exists(p):
        probe_read(f"host path {p}", p)
    else:
        verdict(f"host path {p}", False, "absent(ENOENT) — not on this rootfs")

print("=== positive discriminator: are we looking at the sandbox root? ===")
# If we ever escaped, these would show the host's identity instead of the box's.
for name, path, want in (("/etc/hostname", "/etc/hostname", "sandbox"),
                         ("kernel hostname", "/proc/sys/kernel/hostname", "sandbox")):
    try:
        got = open(path).read().strip()
    except OSError as e:
        got = f"<{why(e.errno)}>"
    # ESCAPED if it reveals the host identity rather than the sandbox's
    leaked = any(m in got for m in HOST_MARKERS)
    verdict(f"{name} identity", leaked, f"value={got!r} want~={want!r}")
rel = os.uname().release
verdict("uname reveals host kernel", any(m in rel.lower() for m in ("microsoft", "6.6")),
        f"release={rel!r}")
uid, gid = os.getuid(), os.getgid()
verdict("dropped to unprivileged uid", uid == 0, f"uid={uid} gid={gid} (want 65534)")

print("=== environment / mounts: eyeball the actual contents, don't trust a bool ===")
env_hits = {k: v for k, v in os.environ.items()
            if any(m in f"{k}={v}" for m in HOST_MARKERS)}
verdict("environ leaks host vars", bool(env_hits),
        f"suspicious={env_hits or 'none'}; keys={sorted(os.environ)}")
try:
    root = sorted(os.listdir("/"))
except OSError as e:
    root = f"<{why(e.errno)}>"
verdict("root listing exposes host mounts", isinstance(root, list) and
        any(d in root for d in ("mnt", "host", "home")),
        f"ls / = {root}")
_environ = read_bytes("/proc/self/environ")
_env_hits = [m for m in HOST_MARKERS if m.encode() in _environ]
verdict("/proc/self/environ secrets", bool(_env_hits),
        f"len={len(_environ)}B host-markers={_env_hits or 'none'}")

print("=== kernel memory / info leaks (want: unreadable) ===")
for p in ("/proc/kcore", "/dev/mem", "/dev/kmem"):
    try:
        with open(p, "rb") as f:
            verdict(f"read {p}", any(f.read(64)), "returned bytes")
    except OSError as e:
        verdict(f"read {p}", False, why(e.errno))

print("=== privileged syscalls (want: all refused) ===")
probe_syscall("ptrace(TRACEME)", libc.ptrace(0, 0, 0, 0), escaped_when_zero=False)
probe_syscall("mount tmpfs", libc.mount(b"none", b"/box/m", b"tmpfs", 0, None))
probe_syscall("unshare(CLONE_NEWUSER)", libc.unshare(0x10000000))
probe_syscall("unshare(CLONE_NEWNS)", libc.unshare(0x00020000))
probe_syscall("setns(0)", libc.setns(0, 0))
probe_syscall("bpf(PROG_LOAD)", libc.syscall(321, 5, 0, 0), escaped_when_zero=False)
# setuid(0): success (rc==0) would mean we regained root
probe_syscall("setuid(0)", libc.setuid(0))

print("=== symlink escape: prove the link RESOLVES, then prove it stays in-box ===")
try:
    lp = "/box/rootlink"
    if os.path.lexists(lp):
        os.unlink(lp)
    os.symlink("/", lp)
    # positive control: reading <link>/etc/hostname MUST work and MUST be the
    # sandbox's own hostname. That proves the symlink resolves (so a null result
    # elsewhere is real containment, not a dangling link) AND that '/' is the
    # sandbox root, not the host's.
    linked_host = open(lp + "/etc/hostname").read().strip()
    leaked = any(m in linked_host for m in HOST_MARKERS)
    verdict("symlink-to-root escape", leaked,
            f"link resolves; <link>/etc/hostname={linked_host!r}")
except OSError as e:
    verdict("symlink-to-root escape", False, f"{why(e.errno)} (link unusable)")

print("=== runc CVE-2019-5736 style: overwrite the runtime via /proc/self/exe ===")
try:
    fd = os.open("/proc/self/exe", os.O_WRONLY)
    os.close(fd)
    verdict("/proc/self/exe writable", True, "opened O_WRONLY")
except OSError as e:
    verdict("/proc/self/exe writable", False, why(e.errno))

print("=== host reboot / privileged procfs writes ===")
for p in ("/proc/sysrq-trigger", "/proc/sys/kernel/hostname"):
    try:
        open(p, "w").write("0")
        verdict(f"write {p}", True, "write succeeded")
    except OSError as e:
        verdict(f"write {p}", False, why(e.errno))

print("=== cross-namespace kill: must not reach host processes ===")
try:
    os.kill(-1, 0)  # signal 0 to every reachable process
    verdict("kill(-1) reached host", False, "only in-namespace pids reachable")
except OSError as e:
    verdict("kill(-1)", False, why(e.errno))

print(f"ADVERSARY DONE (escaped={_escapes})")
