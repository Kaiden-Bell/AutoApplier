"""API tests with the LLM and compiler stubbed out (no network, no pdflatex needed)."""

import base64
import time

import pytest
from fastapi.testclient import TestClient

from backend import compile_adapter, llm_engine
from backend.config import Settings
from backend.main import create_app
from backend.schemas import BulletPick, EntryPick, SectionPick, TailoredResume
from cv_fixtures import projects_and_experience_cv

KEY = "test-key"
HEADERS = {"X-AutoApplier-Key": KEY}
CV = projects_and_experience_cv()


def selection(*sections, qualified=True):
    return TailoredResume(is_qualified=qualified, gap_analysis="Strong match.", sections=[
        SectionPick(section_id=sid, entries=[
            EntryPick(entry_id=eid, bullets=[BulletPick(bullet_id=b, rewrite=None) for b in bullets])
            for eid, bullets in entries
        ]) for sid, entries in sections
    ])


GOOD_SELECTION = selection(
    ("s3", [("s3.e1", ["s3.e1.b1"])]),
    ("s4", [("s4.e1", ["s4.e1.b1"])]),
    ("s5", [("s5.e1", [])]),
)


def payload(title="Software Engineer", location="Reno, NV", pay="$40/hr"):
    return {
        "job": {"title": title, "location": location, "pay": pay, "description": "Build things."},
        "master_cv": CV.model_dump(),
    }


@pytest.fixture
def llm_calls(monkeypatch):
    calls = {"count": 0, "result": GOOD_SELECTION, "raise": None, "cv": None}

    def fake_generate(job, master_cv, model):
        calls["count"] += 1
        calls["cv"] = master_cv
        if calls["raise"]:
            raise calls["raise"]
        return calls["result"]

    monkeypatch.setattr(llm_engine, "generate", fake_generate)
    return calls


@pytest.fixture
def parse_calls(monkeypatch):
    calls = {"count": 0, "raise": None}

    def fake_parse(resume_text, model):
        calls["count"] += 1
        if calls["raise"]:
            raise calls["raise"]
        return CV

    monkeypatch.setattr(llm_engine, "parse_cv", fake_parse)
    return calls


@pytest.fixture
def compile_result(monkeypatch):
    state = {"value": (b"%PDF-1.5 fake", "log ok")}
    monkeypatch.setattr(compile_adapter, "compile_latex_source", lambda src, timeout=30: state["value"])
    return state


@pytest.fixture
def client(llm_calls, parse_calls, compile_result):
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


def test_unconfigured_server_key_fails_closed(llm_calls, parse_calls, compile_result):
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
    latex = result["latex_source"]
    assert "\\section*{Engineering Projects}" in latex and "RLPredictor" in latex
    assert "\\textbf{Test Candidate}" in latex
    assert result["selection_notes"] == []


def test_misfiled_entry_is_rendered_under_its_own_section(client, llm_calls):
    llm_calls["result"] = selection(("s3", [("s3.e1", ["s3.e1.b1"]), ("s4.e1", ["s4.e1.b1"])]),
                                    ("s5", [("s5.e1", [])]))
    result = wait_done(client, submit(client))["result"]
    latex = result["latex_source"]
    assert latex.index("\\section*{Engineering Projects}") < latex.index(
        "\\section*{Professional Experience}") < latex.index("Freelance Web")
    assert any("Moved" in n for n in result["selection_notes"])


def test_client_sent_ids_are_reassigned(client, llm_calls):
    body = payload()
    body["master_cv"]["sections"][0]["id"] = "bogus"
    wait_done(client, submit(client, body))
    assert llm_calls["cv"].sections[0].id == "s1"


def test_empty_selection_is_an_error(client, llm_calls):
    llm_calls["result"] = selection(("s5", [("s5.e1", [])]))
    body = wait_done(client, submit(client))
    assert body["status"] == "error" and "no usable CV content" in body["error"]


def test_missing_master_cv_rejected(client):
    body = payload()
    del body["master_cv"]
    assert client.post("/api/tailor", json=body, headers=HEADERS).status_code == 422


def test_parse_returns_master_cv(client, parse_calls):
    text = "Test Candidate\nEngineering Projects\nRLPredictor\nDesigned an end-to-end machine learning pipeline using replay telemetry."
    r = client.post("/api/cv/parse", json={"resume_text": text}, headers=HEADERS)
    assert r.status_code == 202, r.text
    body = wait_done(client, r.json()["request_id"])
    assert body["status"] == "done" and parse_calls["count"] == 1
    result = body["result"]
    assert result["master_cv"]["sections"][2]["heading"] == "Engineering Projects"
    # Everything except the one bullet that is in the text gets flagged.
    assert not any("replay telemetry" in u for u in result["unmatched_bullets"])
    assert any("77.3%" in u for u in result["unmatched_bullets"])


def test_parse_requires_key_and_text(client):
    assert client.post("/api/cv/parse", json={"resume_text": "x"}).status_code == 401
    assert client.post("/api/cv/parse", json={"resume_text": ""}, headers=HEADERS).status_code == 422


def test_parse_error_marks_request_failed(client, parse_calls):
    parse_calls["raise"] = llm_engine.LLMError("The model found no sections in the CV.")
    r = client.post("/api/cv/parse", json={"resume_text": "x"}, headers=HEADERS)
    body = wait_done(client, r.json()["request_id"])
    assert body["status"] == "error" and "no sections" in body["error"]


def test_not_qualified_returns_gap_analysis(client, llm_calls):
    llm_calls["result"] = TailoredResume(is_qualified=False, gap_analysis="Needs Rust.", sections=[])
    result = wait_done(client, submit(client))["result"]
    assert result["stage"] == "llm" and result["is_qualified"] is False
    assert result["gap_analysis"] == "Needs Rust." and result["resume_pdf_base64"] == ""


def test_compile_failure_returns_source_and_log(client, compile_result):
    compile_result["value"] = (None, "! Undefined control sequence.")
    result = wait_done(client, submit(client))["result"]
    assert result["stage"] == "compile"
    assert "RLPredictor" in result["latex_source"]
    assert "Undefined control sequence" in result["compile_log"]


def test_latex_in_cv_text_is_escaped_not_executed(client, compile_result):
    body = payload()
    body["master_cv"]["sections"][2]["entries"][0]["bullets"][0]["text"] = "\\write18{ls}"
    result = wait_done(client, submit(client, body))["result"]
    assert result["stage"] == "complete"
    assert "\\textbackslash{}write18\\{ls\\}" in result["latex_source"]


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
    body["master_cv"]["sections"][2]["entries"][0]["bullets"][0]["text"] = "x" * 2_001
    assert client.post("/api/tailor", json=body, headers=HEADERS).status_code == 422


def test_unknown_request_404(client):
    assert client.get("/api/requests/nope", headers=HEADERS).status_code == 404


def test_overlong_resume_is_reported(client, compile_result):
    compile_result["value"] = (b"%PDF-1.5", "Output written on resume.pdf (2 pages, 999 bytes).")
    result = wait_done(client, submit(client))["result"]
    assert result["stage"] == "complete" and result["page_count"] == 2
    assert "2 pages" in result["reason"]


def test_one_page_resume_reports_page_count(client, compile_result):
    compile_result["value"] = (b"%PDF-1.5", "Output written on resume.pdf (1 page, 999 bytes).")
    result = wait_done(client, submit(client))["result"]
    assert result["page_count"] == 1 and result["reason"] == "Resume generated."
