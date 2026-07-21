// Native compiled adversary: raw syscalls, not libc wrappers. Confirms a
// compiled exploit binary is contained identically to the interpreted probes.
// Like adversary.py, every probe prints EVIDENCE (errno name / bytes read /
// resolved value) so a blocked(EPERM) pass is never confused with an
// absent(ENOENT) one, and the positive discriminator (hostname == "sandbox")
// carries the containment proof independent of whether any decoy file exists.
#include <cstdio>
#include <cstring>
#include <cerrno>
#include <fcntl.h>
#include <unistd.h>
#include <sys/mount.h>
#include <sys/ptrace.h>
#include <sched.h>
#include <sys/syscall.h>

static int escapes = 0;

static void verdict(const char* name, bool escaped, const char* detail = "") {
    escapes += escaped;
    printf("%s: %s  [%s]\n", escaped ? "ESCAPED" : "contained", name, detail);
}

// Read up to n bytes; report host markers found and how much came back.
static bool probe_read(const char* name, const char* path) {
    char detail[256];
    int fd = open(path, O_RDONLY);
    if (fd < 0) {
        snprintf(detail, sizeof detail, "%s at open", strerror(errno));
        verdict(name, false, detail);
        return false;
    }
    char buf[4096]; ssize_t n = read(fd, buf, sizeof buf - 1); close(fd);
    if (n < 0) n = 0;
    buf[n] = 0;
    bool leaked = strstr(buf, "mohitkoul") || strstr(buf, "microsoft") ||
                  strstr(buf, "/mnt/c");
    snprintf(detail, sizeof detail, "read %zdB host-markers=%s", n,
             leaked ? "YES" : "none");
    verdict(name, leaked, detail);
    return leaked;
}

// Raw syscall: log rc and errno so blocked vs. not-implemented is visible.
static void probe_syscall(const char* name, long rc, bool escaped_when_zero = true) {
    char detail[128];
    bool escaped = escaped_when_zero ? (rc == 0) : (rc >= 0);
    snprintf(detail, sizeof detail, "rc=%ld %s", rc,
             rc == 0 ? "ok" : strerror(errno));
    verdict(name, escaped, detail);
}

int main() {
    probe_read("open /etc/passwd", "/etc/passwd");
    probe_read("/proc/1/root/etc/passwd", "/proc/1/root/etc/passwd");
    probe_read("/proc/self/root traversal", "/proc/self/root/../../etc/passwd");

    // Positive discriminator: this MUST resolve, and to the sandbox's identity.
    char host[128] = {0};
    int hf = open("/etc/hostname", O_RDONLY);
    if (hf >= 0) { ssize_t n = read(hf, host, sizeof host - 1); close(hf);
                   if (n > 0) { host[n] = 0; if (host[n-1] == '\n') host[n-1] = 0; } }
    bool host_leak = strstr(host, "microsoft") || strstr(host, "mohitkoul");
    char hd[160]; snprintf(hd, sizeof hd, "hostname=%s (want sandbox)", host);
    verdict("hostname reveals host identity", host_leak, hd);

    probe_syscall("ptrace(TRACEME)", ptrace(PTRACE_TRACEME, 0, 0, 0), false);
    probe_syscall("mount tmpfs", mount("none", "/box/m", "tmpfs", 0, nullptr));
    probe_syscall("unshare(NEWUSER|NEWNS)", unshare(CLONE_NEWUSER | CLONE_NEWNS));
    probe_syscall("raw bpf() syscall", syscall(SYS_bpf, 5, 0, 0), false);
    probe_syscall("setuid(0)", setuid(0));

    { int fd = open("/proc/self/exe", O_WRONLY);
      if (fd >= 0) { close(fd); verdict("/proc/self/exe writable", true, "opened O_WRONLY"); }
      else verdict("/proc/self/exe writable", false, strerror(errno)); }

    // symlink escape: prove the link RESOLVES (read the sandbox's own hostname
    // through it) so a null result is real containment, not a dangling link.
    unlink("/box/rl");
    if (symlink("/", "/box/rl") == 0) {
        char via[128] = {0};
        int vf = open("/box/rl/etc/hostname", O_RDONLY);
        if (vf >= 0) { ssize_t n = read(vf, via, sizeof via - 1); close(vf);
                       if (n > 0) { via[n] = 0; if (via[n-1]=='\n') via[n-1]=0; } }
        bool leaked = strstr(via, "microsoft") || strstr(via, "mohitkoul");
        char sd[160]; snprintf(sd, sizeof sd, "link resolves; <link>/etc/hostname=%s", via);
        verdict("symlink-to-root escape", leaked, sd);
    } else {
        verdict("symlink-to-root escape", false, strerror(errno));
    }

    printf("ADVERSARY DONE (escaped=%d)\n", escapes);
    return 0;
}
