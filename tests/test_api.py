"""Integration: REST API end-to-end via the app (real gVisor for /execute)."""
import time

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.flaky(reruns=2, reruns_delay=1)]

PY = {"language": "python", "files": [{"name": "main.py", "content": "print(6*7)"}]}


def test_healthz_languages_stats(client):
    assert client.get("/api/v1/healthz").json()["status"] == "ok"
    langs = {l["name"] for l in client.get("/api/v1/languages").json()["languages"]}
    assert {"python", "javascript", "cpp"} <= langs
    stats = client.get("/api/v1/stats").json()
    assert "queue" in stats and "rate_limit" in stats


def test_execute_returns_output_and_history_id(client):
    r = client.post("/api/v1/execute", json=PY)
    assert r.status_code == 200
    body = r.json()
    assert body["stdout"].strip() == "42" and body["exit_code"] == 0
    # persisted and retrievable
    if body.get("id"):
        got = client.get(f"/api/v1/runs/{body['id']}")
        assert got.status_code == 200
        assert got.json()["language"] == "python"


def test_async_job_flow(client):
    rid = client.post("/api/v1/jobs", json=PY).json()["id"]
    for _ in range(100):
        j = client.get(f"/api/v1/jobs/{rid}").json()
        if j["status"] in ("done", "error"):
            break
        time.sleep(0.1)
    j = client.get(f"/api/v1/jobs/{rid}").json()
    assert j["status"] == "done"
    assert j["result"]["stdout"].strip() == "42"


def test_multifile_sibling_import(client):
    # the entrypoint imports a second staged file (sys.path includes /src)
    body = {"language": "python", "files": [
        {"name": "main.py", "content": "import helper\nprint(helper.answer())"},
        {"name": "helper.py", "content": "def answer():\n    return 6 * 7"}]}
    r = client.post("/api/v1/execute", json=body)
    assert r.status_code == 200
    assert r.json()["stdout"].strip() == "42"


def test_multifile_go_package(client):
    # two files in the same `package main` compile together (go build -C /src .)
    body = {"language": "go", "files": [
        {"name": "main.go",
         "content": 'package main\nimport "fmt"\nfunc main() { fmt.Println(msg()) }'},
        {"name": "msg.go",
         "content": 'package main\nfunc msg() string { return "go multifile ok" }'}]}
    r = client.post("/api/v1/execute", json=body)
    assert r.status_code == 200
    assert "go multifile ok" in r.json()["stdout"], r.json()


def test_languages_expose_main_file(client):
    langs = client.get("/api/v1/languages").json()["languages"]
    by = {l["name"]: l["main_file"] for l in langs}
    assert by["python"] == "main.py" and by["cpp"] == "main.cpp"


def test_unknown_job_and_run_404(client):
    assert client.get("/api/v1/jobs/deadbeef").status_code == 404
    assert client.get("/api/v1/runs/deadbeef").status_code == 404


@pytest.mark.parametrize("body,want", [
    ({"language": "cobol", "files": [{"name": "m", "content": "x"}]}, 400),
    ({"language": "python", "files": []}, 422),
    ({"language": "python"}, 422),
    ({"language": "python", "files": [{"name": "../x", "content": "x"}]}, 422),
])
def test_malformed_requests_are_4xx(client, body, want):
    assert client.post("/api/v1/execute", json=body).status_code == want
