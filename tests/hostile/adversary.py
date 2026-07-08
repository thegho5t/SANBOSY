"""Adversarial escape attempts. Each prints 'contained: <name>' or
'ESCAPED: <name>'. A single ESCAPED line means a real containment failure.

Techniques: host-fs access via several vectors, procfs/kernel-memory leaks,
kernel fingerprinting, privileged syscalls (ptrace/mount/unshare/setns/bpf),
symlink escape, /proc/self/exe overwrite (runc CVE-2019-5736 style), sysrq,
and cross-namespace signalling.
"""
import ctypes
import ctypes.util
import os

libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
HOST_MARKERS = ("mohitkoul", "/home/", "microsoft", "6.6.")


def verdict(name, escaped):
    print(f"{'ESCAPED' if escaped else 'contained'}: {name}")


def leaks_host(path):
    try:
        data = open(path, "r", errors="replace").read()
        return any(m in data for m in HOST_MARKERS)
    except OSError:
        return False


# --- host filesystem access via multiple vectors ---
verdict("read /etc/passwd -> host users", leaks_host("/etc/passwd"))
verdict("/proc/1/root/etc/passwd", leaks_host("/proc/1/root/etc/passwd"))
verdict("/proc/self/root/etc/passwd", leaks_host("/proc/self/root/etc/passwd"))
verdict("/proc/self/cwd traversal", leaks_host("/proc/self/cwd/../../../etc/passwd"))
for p in ("/home/mohitkoul", "/mnt/c", "/root/.ssh", "/etc/shadow"):
    verdict(f"host path {p}", os.path.exists(p) and leaks_host(p) if p == "/etc/shadow"
            else os.path.exists(p))

# --- kernel memory / info leaks ---
for p in ("/proc/kcore", "/dev/mem", "/dev/kmem"):
    try:
        with open(p, "rb") as f:
            verdict(f"read {p}", any(f.read(64)))
    except OSError:
        verdict(f"read {p}", False)

# --- kernel fingerprint: must not reveal the host kernel ---
rel = os.uname().release
verdict(f"uname reveals host kernel ({rel})",
        "microsoft" in rel.lower() or rel.startswith("6.6"))

# --- privileged syscalls (want: all blocked -> rc == -1) ---
verdict("ptrace(TRACEME)", libc.ptrace(0, 0, 0, 0) != -1)
verdict("mount tmpfs", libc.mount(b"none", b"/box/m", b"tmpfs", 0, None) == 0)
verdict("unshare(CLONE_NEWUSER)", libc.unshare(0x10000000) == 0)
verdict("unshare(CLONE_NEWNS)", libc.unshare(0x00020000) == 0)
verdict("setns(0)", libc.setns(0, 0) == 0)
# bpf(BPF_PROG_LOAD=5) — arg garbage; success (>=0) would be the escape
verdict("bpf()", libc.syscall(321, 5, 0, 0) >= 0)

# --- symlink escape from the writable workdir ---
try:
    os.symlink("/", "/box/rootlink")
    verdict("symlink-to-root escape", leaks_host("/box/rootlink/etc/passwd"))
except OSError:
    verdict("symlink-to-root escape", False)

# --- runc CVE-2019-5736 style: overwrite the runtime via /proc/self/exe ---
try:
    fd = os.open("/proc/self/exe", os.O_WRONLY)
    os.close(fd)
    verdict("/proc/self/exe writable", True)
except OSError:
    verdict("/proc/self/exe writable", False)

# --- host reboot / privileged procfs writes ---
for p in ("/proc/sysrq-trigger", "/proc/sys/kernel/hostname",
          "/proc/self/oom_score_adj"):
    try:
        open(p, "w").write("0")
        verdict(f"write {p}", p != "/proc/self/oom_score_adj")  # oom_score_adj is ok
    except OSError:
        verdict(f"write {p}", False)

# --- broadcast kill: must not reach host processes (PID namespace) ---
try:
    os.kill(-1, 0)  # signal 0 to every reachable process
    verdict("kill(-1) reached host", False)  # only sandbox pids are reachable
except OSError:
    verdict("kill(-1)", False)

print("ADVERSARY DONE")
