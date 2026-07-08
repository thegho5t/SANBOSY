import ctypes, ctypes.util, os

libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)

def call(name, *args):
    fn = getattr(libc, name)
    rc = fn(*args)
    return rc, ctypes.get_errno()

# ptrace(PTRACE_TRACEME=0)
rc, e = call("ptrace", 0, 0, 0, 0)
print("ptrace:", "blocked(EPERM)" if rc == -1 and e == 1 else f"rc={rc} errno={e}")

# unshare(CLONE_NEWUSER=0x10000000)
rc, e = call("unshare", 0x10000000)
print("unshare:", "blocked(EPERM)" if rc == -1 and e == 1 else f"rc={rc} errno={e}")

# a normal syscall must still work
print("getpid:", os.getpid() > 0 and "ok")
