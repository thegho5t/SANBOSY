"""Unit tests for API request validation (pydantic models)."""
import pytest
from pydantic import ValidationError

from app.api.schemas import ExecuteRequest, MAX_FILES, STDIN_CAP


def _req(**kw):
    base = {"language": "python", "files": [{"name": "main.py", "content": "x"}]}
    base.update(kw)
    return ExecuteRequest(**base)


def test_valid_request():
    r = _req(stdin="hi", run_timeout_ms=1000)
    assert r.language == "python"
    assert r.files[0].name == "main.py"


def test_empty_files_rejected():
    with pytest.raises(ValidationError):
        _req(files=[])


def test_too_many_files_rejected():
    files = [{"name": f"f{i}.py", "content": "x"} for i in range(MAX_FILES + 1)]
    with pytest.raises(ValidationError):
        _req(files=files)


@pytest.mark.parametrize("name", ["../evil", "a/b", "a\\b", "..", ".", "a\tb", "a\nb", ""])
def test_bad_filenames_rejected(name):
    with pytest.raises(ValidationError):
        _req(files=[{"name": name, "content": "x"}])


def test_oversized_stdin_rejected():
    with pytest.raises(ValidationError):
        _req(stdin="s" * (STDIN_CAP + 1))


@pytest.mark.parametrize("ms", [99, 0, -5, 30_001, 999_999])
def test_run_timeout_bounds(ms):
    with pytest.raises(ValidationError):
        _req(run_timeout_ms=ms)


def test_duplicate_filenames_rejected():
    with pytest.raises(ValidationError):
        _req(files=[{"name": "a.py", "content": "x"},
                    {"name": "a.py", "content": "y"}])
