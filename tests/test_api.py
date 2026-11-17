"""API tests with the LLM and compiler stubbed out (no network, no pdflatex needed)."""

import base64
import time

import pytest
from fastapi.testclient import TestClient

from backend import compile_adapter, llm_engine
from backend.config import Settings
from backend.main import create_app
from backend.schemas import LLMResult

KEY = "test-key"
HEADERS = {"X-AutoApplier-Key": KEY}
VALID_LATEX = "\\documentclass{article}\n\\begin{document}\nHi\n\\end{document}\n"


def payload(title="Software Engineer", location="Reno, NV", pay="$40/hr"):
    return {
        "job": {"title": title, "location": location, "pay": pay, "description": "Build things."},
        "resume_text": "Experienced builder of things.",
    }


@pytest.fixture
def llm_calls(monkeypatch):
    calls = {"count": 0, "result": LLMResult(is_qualified=True, gap_analysis="Strong match.",
                                            latex_code=VALID_LATEX), "raise": None}

    def fake_generate(job, resume_text, model):
        calls["count"] += 1
        if calls["raise"]:
            raise calls["raise"]
        return calls["result"]

    monkeypatch.setattr(llm_engine, "generate", fake_generate)
    return calls


@pytest.fixture
def compile_result(monkeypatch):
    state = {"value": (b"%PDF-1.5 fake", "log ok")}
    monkeypatch.setattr(compile_adapter, "compile_latex_source", lambda src, timeout=30: state["value"])
    return state


@pytest.fixture
def client(llm_calls, compile_result):
    app = create_app(Settings(api_key=KEY, rate_limit_per_minute=3, worker_count=1, job_ttl_seconds=60))
    with TestClient(app) as c:
        yield c


def wait_done(client, request_id, timeout=5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"/api/requests/{request_id}", headers=HEADERS)
        assert r.status_code == 200
        body = r.json()
        if body["status"] != "running":
            return body
        time.sleep(0.02)
    raise AssertionError("request did not finish")


def submit(client, body=None, headers=HEADERS):
    r = client.post("/api/tailor", json=body or payload(), headers=headers)
    assert r.status_code == 202, r.text
    return r.json()["request_id"]


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["ok"] is True


@pytest.mark.parametrize("headers", [{}, {"X-AutoApplier-Key": "wrong"}])
def test_rejects_missing_or_bad_key(client, headers):
    assert client.post("/api/tailor", json=payload(), headers=headers).status_code == 401
    assert client.get("/api/requests/abc", headers=headers).status_code == 401


def test_unconfigured_server_key_fails_closed(llm_calls, compile_result):
    with TestClient(create_app(Settings(api_key=""))) as c:
        assert c.post("/api/tailor", json=payload(), headers=HEADERS).status_code == 503


def test_criteria_rejection_skips_llm(client, llm_calls):
    r = client.post("/api/tailor", json=payload(location="Phoenix, AZ"), headers=HEADERS)
    assert r.status_code == 200
    result = r.json()["result"]
    assert result["stage"] == "criteria" and result["is_qualified"] is False
    assert "Phoenix" in result["reason"] or "phoenix" in result["reason"]
    assert llm_calls["count"] == 0


def test_request_criteria_override_defaults(client, llm_calls):
    body = payload(title="Barista", location="Phoenix, AZ")
    body["criteria"] = {"target_tokens": ["barista"], "allowed_locations": ["phoenix"]}
    request_id = submit(client, body)
    assert wait_done(client, request_id)["status"] == "done"
    assert llm_calls["count"] == 1


def test_success_returns_pdf(client):
    body = wait_done(client, submit(client))
    result = body["result"]
    assert body["status"] == "done" and result["stage"] == "complete"
    assert base64.b64decode(result["resume_pdf_base64"]).startswith(b"%PDF")
    assert result["latex_source"] == VALID_LATEX


def test_not_qualified_returns_gap_analysis(client, llm_calls):
    llm_calls["result"] = LLMResult(is_qualified=False, gap_analysis="Needs Rust.", latex_code="")
    result = wait_done(client, submit(client))["result"]
    assert result["stage"] == "llm" and result["is_qualified"] is False
    assert result["gap_analysis"] == "Needs Rust." and result["resume_pdf_base64"] == ""


def test_compile_failure_returns_source_and_log(client, compile_result):
    compile_result["value"] = (None, "! Undefined control sequence.")
    result = wait_done(client, submit(client))["result"]
    assert result["stage"] == "compile"
    assert result["latex_source"] == VALID_LATEX
    assert "Undefined control sequence" in result["compile_log"]


def test_unsafe_latex_is_not_compiled(client, llm_calls, monkeypatch):
    llm_calls["result"] = LLMResult(is_qualified=True, gap_analysis="",
                                    latex_code=VALID_LATEX.replace("Hi", "\\write18{ls}"))
    monkeypatch.setattr(compile_adapter, "compile_latex_source",
                        lambda *a, **k: pytest.fail("compiler should not run"))
    result = wait_done(client, submit(client))["result"]
    assert result["stage"] == "compile" and "write18" in result["compile_log"]


def test_llm_error_marks_request_failed(client, llm_calls):
    llm_calls["raise"] = llm_engine.LLMError("The model refused the request: nope")
    body = wait_done(client, submit(client))
    assert body["status"] == "error" and "refused" in body["error"]


def test_idempotency_key_dedupes_and_conflicts(client, llm_calls):
    headers = {**HEADERS, "Idempotency-Key": "abc-123"}
    first = submit(client, headers=headers)
    second = submit(client, headers=headers)
    assert first == second
    wait_done(client, first)
    assert llm_calls["count"] == 1

    other = payload(title="Backend Developer")
    r = client.post("/api/tailor", json=other, headers=headers)
    assert r.status_code == 409


def test_rate_limit(client):
    for _ in range(3):
        submit(client)
    assert client.post("/api/tailor", json=payload(), headers=HEADERS).status_code == 429


def test_rate_limit_ignores_criteria_rejections(client):
    for _ in range(5):
        r = client.post("/api/tailor", json=payload(location="Phoenix, AZ"), headers=HEADERS)
        assert r.status_code == 200
    submit(client)


def test_oversized_input_rejected(client):
    body = payload()
    body["resume_text"] = "x" * 50_001
    assert client.post("/api/tailor", json=body, headers=HEADERS).status_code == 422


def test_unknown_request_404(client):
    assert client.get("/api/requests/nope", headers=HEADERS).status_code == 404
