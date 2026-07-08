"""Integration: SSE streaming endpoint delivers live chunks then a done event."""
import json

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.flaky(reruns=2, reruns_delay=1)]


def _events(client, body):
    evs = []
    with client.stream("POST", "/api/v1/execute/stream", json=body) as r:
        assert r.status_code == 200
        assert "text/event-stream" in r.headers["content-type"]
        for line in r.iter_lines():
            line = line if isinstance(line, str) else line.decode()
            if line.startswith("data: "):
                evs.append(json.loads(line[6:]))
    return evs


def test_stream_stdout_then_done(client):
    evs = _events(client, {"language": "python", "files": [
        {"name": "main.py", "content": "print('alpha'); print('beta')"}]})
    types = [e["type"] for e in evs]
    assert "stdout" in types and types[-1] == "done"
    out = "".join(e["data"] for e in evs if e["type"] == "stdout")
    assert "alpha" in out and "beta" in out
    assert evs[-1]["exit_code"] == 0


def test_stream_reports_stderr_and_compile_error(client):
    # a C++ compile error streams stderr then a done event with stage=compile
    evs = _events(client, {"language": "cpp", "files": [
        {"name": "main.cpp", "content": "this is not valid c++"}]})
    assert evs[-1]["type"] == "done"
    assert evs[-1]["stage"] == "compile" and evs[-1]["exit_code"] != 0
