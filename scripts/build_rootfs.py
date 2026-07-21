"""Populate the shared read-only rootfs skeleton.

Rootless gVisor on this WSL2 kernel cannot bind-mount pre-existing top-level
host dirs (the root mount is locked), so we copy each toolchain's file closure
into the rootfs once. Per-run, only the fresh /box dir is bind-mounted (that
works). This is the standard container-image model and is how Piston-package
toolchains (M3) get added: point --copy-tree at the unpacked package dir.
"""
import argparse
import os
import shutil
import subprocess
from pathlib import Path

RUNTIME_ROOT = Path(os.environ.get("SANDBOX_RUNTIME_ROOT",
                                   str(Path.home() / ".sandbox")))
ROOTFS = RUNTIME_ROOT / "rootfs"


def ldd_closure(binaries: list[str]) -> set[str]:
    libs: set[str] = set()
    for b in binaries:
        try:
            out = subprocess.check_output(["ldd", b], text=True, stderr=subprocess.DEVNULL)
        except subprocess.CalledProcessError:
            continue
        for line in out.splitlines():
            line = line.strip()
            if "=>" in line:
                p = line.split("=>", 1)[1].strip().split(" ")[0]
            else:
                p = line.split(" ")[0]
            if p.startswith("/"):
                libs.add(p)
    return libs


def _copy_one(src_p: Path, dst: Path) -> None:
    """Idempotently copy a single entry (file/symlink), overwriting stale
    symlinks and skipping files that already exist."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src_p.is_symlink():
        link = os.readlink(src_p)
        if dst.is_symlink() or dst.exists():
            if dst.is_symlink() and os.readlink(dst) == link:
                return
            if dst.is_dir() and not dst.is_symlink():
                return
            dst.unlink()
        os.symlink(link, dst)
    else:
        if dst.exists():
            return
        shutil.copy2(src_p, dst, follow_symlinks=False)


def copy_into_rootfs(src: str) -> None:
    """Copy an absolute host path into ROOTFS at the same relative location,
    preserving symlinks. Idempotent across repeated toolchain adds."""
    src_p = Path(src)
    dst = ROOTFS / src_p.relative_to("/")
    if src_p.is_dir() and not src_p.is_symlink():
        for root, dirs, files in os.walk(src_p):
            rp = Path(root)
            rel = rp.relative_to(src_p)
            (dst / rel).mkdir(parents=True, exist_ok=True)
            for name in files + dirs:
                s = rp / name
                if s.is_symlink() or s.is_file():
                    _copy_one(s, dst / rel / name)
    else:
        _copy_one(src_p, dst)
        if src_p.is_symlink():  # also bring the resolved target
            copy_into_rootfs(os.path.realpath(src_p))


def ensure_skeleton() -> None:
    for d in ("proc", "dev", "tmp", "box", "etc",
              "usr/bin", "usr/sbin", "usr/lib", "usr/lib64"):
        (ROOTFS / d).mkdir(parents=True, exist_ok=True)
    # usrmerge symlinks so /bin, /lib resolve inside the rootfs
    for link, target in (("bin", "usr/bin"), ("sbin", "usr/sbin"),
                         ("lib", "usr/lib"), ("lib64", "usr/lib64")):
        lp = ROOTFS / link
        if not lp.exists() and not lp.is_symlink():
            lp.symlink_to(target)
    # minimal /etc — deliberately NO passwd/group. They contain nothing sensitive
    # (host accounts never appear here; the sandbox is fully isolated), but a
    # decoy /etc/passwd *looks* like a breach when someone reads it and alarms
    # users. Omitting them makes an escape attempt fail with a clean "No such
    # file" error instead of returning realistic-looking content. All languages
    # run fine without them.
    etc = ROOTFS / "etc"
    for stale in ("passwd", "group"):        # remove if a prior build created them
        (etc / stale).unlink(missing_ok=True)
    (etc / "hostname").write_text("sandbox\n")
    (etc / "hosts").write_text("127.0.0.1 localhost\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--bin", action="append", default=[],
                    help="binary to include with its ldd closure")
    ap.add_argument("--tree", action="append", default=[],
                    help="directory subtree to copy verbatim")
    ap.add_argument("--link", action="append", default=[],
                    help="symlink to create in rootfs as 'linkpath:target'")
    args = ap.parse_args()

    ensure_skeleton()
    for b in args.bin:
        real = os.path.realpath(b)
        copy_into_rootfs(b)
        copy_into_rootfs(real)
    libs = ldd_closure([os.path.realpath(b) for b in args.bin])
    for lib in libs:
        copy_into_rootfs(lib)
        copy_into_rootfs(os.path.realpath(lib))
    for t in args.tree:
        copy_into_rootfs(t)
    for spec in args.link:
        linkpath, target = spec.split(":", 1)
        lp = ROOTFS / linkpath.lstrip("/")
        lp.parent.mkdir(parents=True, exist_ok=True)
        if lp.is_symlink() or lp.exists():
            lp.unlink()
        lp.symlink_to(target)
    print(f"rootfs populated at {ROOTFS}: {len(args.bin)} bins, "
          f"{len(libs)} libs, {len(args.tree)} trees, {len(args.link)} links")


if __name__ == "__main__":
    main()
