// Native compiled adversary: raw syscalls, not libc wrappers. Confirms a
// compiled exploit binary is contained identically to interpreted probes.
#include <cstdio>
#include <cstring>
#include <fcntl.h>
#include <unistd.h>
#include <sys/mount.h>
#include <sys/ptrace.h>
#include <sched.h>
#include <sys/syscall.h>

static void verdict(const char* name, bool escaped) {
    printf("%s: %s\n", escaped ? "ESCAPED" : "contained", name);
}

static bool leaks_host(const char* path) {
    int fd = open(path, O_RDONLY);
    if (fd < 0) return false;
    char buf[4096]; ssize_t n = read(fd, buf, sizeof buf - 1); close(fd);
    if (n <= 0) return false;
    buf[n] = 0;
    return strstr(buf, "mohitkoul") || strstr(buf, "/home/");
}

int main() {
    verdict("open /etc/passwd host users", leaks_host("/etc/passwd"));
    verdict("/proc/1/root/etc/passwd", leaks_host("/proc/1/root/etc/passwd"));
    verdict("ptrace(TRACEME)", ptrace(PTRACE_TRACEME, 0, 0, 0) != -1);
    verdict("mount tmpfs", mount("none", "/box/m", "tmpfs", 0, nullptr) == 0);
    verdict("unshare(NEWUSER|NEWNS)", unshare(CLONE_NEWUSER | CLONE_NEWNS) == 0);
    verdict("raw bpf() syscall", syscall(SYS_bpf, 5, 0, 0) >= 0);
    verdict("/proc/self/exe writable", open("/proc/self/exe", O_WRONLY) >= 0);
    // symlink escape from the writable workdir
    unlink("/box/rl");
    if (symlink("/", "/box/rl") == 0)
        verdict("symlink-to-root escape", leaks_host("/box/rl/etc/passwd"));
    else
        verdict("symlink-to-root escape", false);
    printf("ADVERSARY DONE\n");
    return 0;
}
