"""Run history store (Phase 2, additive).

SQLite-backed (stdlib only, self-hostable, no external service). Persists each
execution — source, stdin, and result — so runs can be listed, retrieved, and
replayed. Toggleable via SANDBOX_PERSIST=0 (Phase 1 throwaway behaviour). Reads
and writes are wrapped in asyncio.to_thread since sqlite3 is synchronous.

Rows are scoped by `identity`: when auth is on, callers see only their own runs;
when auth is off everything runs as the single "local" identity.
"""
import asyncio
import json
import os
import sqlite3
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

RUNTIME_ROOT = Path(os.environ.get("SANDBOX_RUNTIME_ROOT",
                                   str(Path.home() / ".sandbox")))
DB_PATH = RUNTIME_ROOT / "history.db"
PERSIST_ENABLED = os.environ.get("SANDBOX_PERSIST", "1") != "0"

_conn: sqlite3.Connection | None = None
_lock = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id                TEXT PRIMARY KEY,
    created_at        TEXT NOT NULL,
    identity          TEXT NOT NULL,
    language          TEXT NOT NULL,
    files             TEXT NOT NULL,
    stdin             TEXT NOT NULL,
    run_timeout_ms    INTEGER,
    stdout            TEXT NOT NULL,
    stderr            TEXT NOT NULL,
    exit_code         INTEGER,
    timed_out         INTEGER NOT NULL,
    truncated_stdout  INTEGER NOT NULL,
    truncated_stderr  INTEGER NOT NULL,
    wall_time_ms      INTEGER NOT NULL,
    stage             TEXT NOT NULL,
    error             TEXT,
    suspicious        INTEGER NOT NULL DEFAULT 0,
    flags             TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_runs_identity_created
    ON runs (identity, created_at DESC);
"""


@dataclass
class RunRecord:
    identity: str
    language: str
    files: dict[str, str]
    stdin: str
    run_timeout_ms: int | None
    result: dict                      # ExecutionResult.as_dict()
    suspicious: bool = False
    flags: list[str] = field(default_factory=list)
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat())


def init_db() -> None:
    global _conn
    if not PERSIST_ENABLED:
        return
    RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
    _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    with _lock:
        # WAL + a busy timeout so concurrent readers/writers wait instead of
        # erroring (single server in Phase 1, but cheap robustness).
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA busy_timeout=5000")
        _conn.executescript(_SCHEMA)
        # migrate older DBs that predate the abuse columns
        cols = {r["name"] for r in _conn.execute("PRAGMA table_info(runs)")}
        if "suspicious" not in cols:
            _conn.execute("ALTER TABLE runs ADD COLUMN suspicious INTEGER NOT NULL DEFAULT 0")
        if "flags" not in cols:
            _conn.execute("ALTER TABLE runs ADD COLUMN flags TEXT NOT NULL DEFAULT ''")
        _conn.commit()


def close_db() -> None:
    global _conn
    if _conn is not None:
        _conn.close()
        _conn = None


def _save_sync(rec: RunRecord) -> None:
    r = rec.result
    with _lock:
        _conn.execute(
            """INSERT INTO runs VALUES
               (:id,:created_at,:identity,:language,:files,:stdin,:run_timeout_ms,
                :stdout,:stderr,:exit_code,:timed_out,:truncated_stdout,
                :truncated_stderr,:wall_time_ms,:stage,:error,
                :suspicious,:flags)""",
            {
                "id": rec.id, "created_at": rec.created_at,
                "identity": rec.identity, "language": rec.language,
                "files": json.dumps(rec.files), "stdin": rec.stdin,
                "run_timeout_ms": rec.run_timeout_ms,
                "stdout": r["stdout"], "stderr": r["stderr"],
                "exit_code": r["exit_code"], "timed_out": int(r["timed_out"]),
                "truncated_stdout": int(r["truncated_stdout"]),
                "truncated_stderr": int(r["truncated_stderr"]),
                "wall_time_ms": r["wall_time_ms"], "stage": r["stage"],
                "error": r["error"],
                "suspicious": int(rec.suspicious), "flags": ",".join(rec.flags),
            })
        _conn.commit()


def _list_sync(identity: str, limit: int, offset: int) -> list[dict]:
    with _lock:
        rows = _conn.execute(
            """SELECT id, created_at, language, exit_code, timed_out, stage,
                      wall_time_ms, suspicious, flags FROM runs
               WHERE identity = ? ORDER BY created_at DESC LIMIT ? OFFSET ?""",
            (identity, limit, offset)).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        d["suspicious"] = bool(d["suspicious"])
        d["flags"] = [f for f in (d["flags"] or "").split(",") if f]
        result.append(d)
    return result


def _get_sync(identity: str, run_id: str) -> dict | None:
    with _lock:
        row = _conn.execute(
            "SELECT * FROM runs WHERE id = ? AND identity = ?",
            (run_id, identity)).fetchone()
    if row is None:
        return None
    d = dict(row)
    d["files"] = json.loads(d["files"])
    for k in ("timed_out", "truncated_stdout", "truncated_stderr", "suspicious"):
        d[k] = bool(d[k])
    d["flags"] = [f for f in (d["flags"] or "").split(",") if f]
    return d


def _delete_sync(identity: str, run_id: str) -> bool:
    with _lock:
        cur = _conn.execute("DELETE FROM runs WHERE id = ? AND identity = ?",
                            (run_id, identity))
        _conn.commit()
        return cur.rowcount > 0


async def save(rec: RunRecord) -> None:
    if not PERSIST_ENABLED or _conn is None:
        return
    await asyncio.to_thread(_save_sync, rec)


async def list_runs(identity: str, limit: int = 50, offset: int = 0) -> list[dict]:
    if not PERSIST_ENABLED or _conn is None:
        return []
    return await asyncio.to_thread(_list_sync, identity, limit, offset)


async def get_run(identity: str, run_id: str) -> dict | None:
    if not PERSIST_ENABLED or _conn is None:
        return None
    return await asyncio.to_thread(_get_sync, identity, run_id)


async def delete_run(identity: str, run_id: str) -> bool:
    if not PERSIST_ENABLED or _conn is None:
        return False
    return await asyncio.to_thread(_delete_sync, identity, run_id)
