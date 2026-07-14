"""Shared pytest fixtures and integration gating."""
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

RUNSC = shutil.which("runsc") or str(Path.home() / ".local/bin/runsc")


def _runsc_available() -> bool:
    return Path(RUNSC).exists()


def pytest_collection_modifyitems(config, items):
    """Skip @pytest.mark.integration tests when runsc isn't installed, so the unit
    suite still runs anywhere (e.g. CI without gVisor)."""
    if _runsc_available():
        return
    skip = pytest.mark.skip(reason="runsc not available; integration test skipped")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def fresh_store(tmp_path, monkeypatch):
    """A store backed by an isolated temp SQLite DB, initialised and torn down."""
    from app import store
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "history.db")
    monkeypatch.setattr(store, "PERSIST_ENABLED", True)
    store._conn = None
    store.init_db()
    yield store
    store.close_db()


@pytest.fixture
def run_code():
    """Return an async helper that runs source in a real sandbox (integration)."""
    from app.executor.limits import DEFAULT_LIMITS
    from app.executor.runner import ExecutionRequest, execute
    from app.languages.registry import get_language, resolve

    async def _run(lang_name, code, stdin="", timeout=None):
        lang = get_language(lang_name)
        p = resolve(lang, DEFAULT_LIMITS)
        req = ExecutionRequest(
            args=p.run_args, files={lang.main_file: code}, stdin=stdin,
            timeout_s=timeout, limits=p.run_limits,
            compile_args=p.compile_args, compile_limits=p.compile_limits,
            env=p.run_env or None, compile_env=p.compile_env or None,
            compile_cache=p.compile_cache)
        return await execute(req)

    return _run


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Starlette TestClient with the app lifespan active (queue, limiter, etc.).
    Auth is forced OFF and history to a temp DB so the suite is independent of any
    real ~/.sandbox state (e.g. demo keys created by serve_public.sh)."""
    from fastapi.testclient import TestClient
    from app import store
    from app.api import auth
    monkeypatch.setattr(auth, "KEYS_FILE", tmp_path / "no_keys.json")
    monkeypatch.delenv("SANDBOX_API_KEYS", raising=False)
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "hist.db")
    from app.main import app
    with TestClient(app) as c:
        yield c
