"""API contract (Pydantic models). This is the stable v1 seam that Phase 2
(auth, queue, quotas, persistence) attaches to without breaking clients.
"""
from pydantic import BaseModel, Field, field_validator

from ..executor.limits import DEFAULT_LIMITS

MAX_FILES = 16
STDIN_CAP = 256 * 1024  # bytes; bounds orchestrator memory for the stdin pipe


class FileIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    content: str

    @field_validator("name")
    @classmethod
    def _safe_name(cls, v: str) -> str:
        if "/" in v or "\\" in v or v in (".", ".."):
            raise ValueError("file name must be a bare name, no path separators")
        if any(ord(c) < 32 for c in v):
            raise ValueError("file name must not contain control characters")
        return v


class ExecuteRequest(BaseModel):
    language: str
    files: list[FileIn] = Field(..., min_length=1, max_length=MAX_FILES)
    stdin: str = Field("", max_length=STDIN_CAP)
    run_timeout_ms: int | None = Field(default=None, ge=100, le=30_000)

    @field_validator("files")
    @classmethod
    def _unique_names(cls, v: list[FileIn]) -> list[FileIn]:
        names = [f.name for f in v]
        if len(set(names)) != len(names):
            raise ValueError("duplicate file names")
        return v


class ExecuteResponse(BaseModel):
    stdout: str
    stderr: str
    exit_code: int | None
    timed_out: bool
    truncated_stdout: bool
    truncated_stderr: bool
    wall_time_ms: int
    error: str | None = None
    stage: str = "run"          # "compile" if it failed at the compile step
    id: str | None = None       # history id, when persistence is enabled


class RunSummary(BaseModel):
    id: str
    created_at: str
    language: str
    exit_code: int | None
    timed_out: bool
    stage: str
    wall_time_ms: int
    suspicious: bool = False
    flags: list[str] = []


class RunListResponse(BaseModel):
    runs: list[RunSummary]
    limit: int
    offset: int


class JobSubmitResponse(BaseModel):
    id: str
    status: str          # "queued"


class JobStatusResponse(BaseModel):
    id: str
    status: str          # queued | running | done | error
    result: ExecuteResponse | None = None


class RunDetail(RunSummary):
    files: dict[str, str]
    stdin: str
    run_timeout_ms: int | None
    stdout: str
    stderr: str
    truncated_stdout: bool
    truncated_stderr: bool
    error: str | None = None


class LanguageInfo(BaseModel):
    name: str
    main_file: str = ""      # entrypoint filename (e.g. main.py) for multi-file UIs


class LanguagesResponse(BaseModel):
    languages: list[LanguageInfo]
    defaults: dict
    auth_required: bool = False


def default_limits_dict() -> dict:
    lim = DEFAULT_LIMITS
    return {
        "memory_max": lim.memory_max,
        "pids_max": lim.pids_max,
        "cpu_quota_pct": lim.cpu_quota_pct,
        "run_timeout_ms": int(lim.wall_timeout_s * 1000),
        "output_cap_bytes": lim.output_cap_bytes,
        "source_cap_bytes": lim.source_cap_bytes,
    }
