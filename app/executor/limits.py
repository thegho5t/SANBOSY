"""All resource-limit tunables in one place."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Limits:
    memory_max: str = "256M"          # cgroup memory.max
    memory_swap_max: str = "0"        # no swap
    pids_max: int = 64                # fork-bomb cap
    cpu_quota_pct: int = 100          # 100% == 1 core
    wall_timeout_s: float = 5.0       # run step
    compile_timeout_s: float = 10.0   # compile step (C/C++, M3)
    output_cap_bytes: int = 64 * 1024  # per stream (stdout / stderr)
    source_cap_bytes: int = 256 * 1024
    box_file_size_bytes: int = 32 * 1024 * 1024  # RLIMIT_FSIZE inside sandbox
    nofile: int = 256


DEFAULT_LIMITS = Limits()
