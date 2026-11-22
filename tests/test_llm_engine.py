"""LLM engine error-path tests using a fake OpenAI client (no network)."""

from types import SimpleNamespace

import httpx
import openai
import pytest

from backend import llm_engine
from backend.schemas import Job, ParsedCV, TailoredResume
from cv_fixtures import contact, entry, projects_and_experience_cv, section

JOB = Job(title="Software Engineer", location="Reno, NV", pay="$40/hr", description="Build APIs.")
CV = projects_and_experience_cv()


def fake_client(monkeypatch, *, message=None, raises=None, choices=True):
    captured = {}

    def parse(**kwargs):
        captured.update(kwargs)
        if raises:
            raise raises
        return SimpleNamespace(choices=[SimpleNamespace(message=message)] if choices else [])

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(parse=parse)))
    monkeypatch.setattr(llm_engine, "_client", lambda: client)
    return captured


def msg(parsed=None, refusal=None):
    return SimpleNamespace(parsed=parsed, refusal=refusal)


def test_returns_parsed_result_and_sends_strict_schema(monkeypatch):
    result = TailoredResume(is_qualified=True, gap_analysis="ok", sections=[])
    captured = fake_client(monkeypatch, message=msg(parsed=result))
    assert llm_engine.generate(JOB, CV, "test-model") == result
    assert captured["model"] == "test-model"
    assert captured["response_format"] is TailoredResume
    user = captured["messages"][1]["content"]
    assert "Build APIs." in user and "Reno, NV" in user
    assert "ENTRY s4.e1: Freelance Web & Systems Developer" in user
    assert "test@example.com" not in user


def test_parse_cv_assigns_ids_and_uses_parse_prompt(monkeypatch):
    parsed = ParsedCV(contact=contact(), sections=[
        section("Projects", "projects", entry("Thing", ["Did a thing.", "  "])),
    ])
    captured = fake_client(monkeypatch, message=msg(parsed=parsed))
    cv = llm_engine.parse_cv("PROJECTS\nThing\n- Did a thing.", "m")
    assert captured["response_format"] is ParsedCV
    assert "transcriber" in captured["messages"][0]["content"]
    assert cv.sections[0].id == "s1"
    assert [b.id for b in cv.sections[0].entries[0].bullets] == ["s1.e1.b1"]


def test_parse_cv_with_no_sections_fails(monkeypatch):
    fake_client(monkeypatch, message=msg(parsed=ParsedCV(contact=contact(), sections=[])))
    with pytest.raises(llm_engine.LLMError, match="no sections"):
        llm_engine.parse_cv("cv", "m")


def test_refusal(monkeypatch):
    fake_client(monkeypatch, message=msg(refusal="I can't help with that."))
    with pytest.raises(llm_engine.LLMError, match="refused"):
        llm_engine.generate(JOB, CV, "m")


def test_unparseable_output(monkeypatch):
    fake_client(monkeypatch, message=msg(parsed=None))
    with pytest.raises(llm_engine.LLMError, match="expected format"):
        llm_engine.generate(JOB, CV, "m")


def test_no_choices(monkeypatch):
    fake_client(monkeypatch, choices=False)
    with pytest.raises(llm_engine.LLMError, match="no choices"):
        llm_engine.generate(JOB, CV, "m")


def test_upstream_api_failure(monkeypatch):
    err = openai.APIConnectionError(request=httpx.Request("POST", "https://api.openai.com"))
    fake_client(monkeypatch, raises=err)
    with pytest.raises(llm_engine.LLMError, match="APIConnectionError"):
        llm_engine.generate(JOB, CV, "m")


def test_prompt_files_load():
    assert "is_qualified" in llm_engine._system_prompt()
    assert "section_id" in llm_engine._system_prompt()
    assert "kind" in llm_engine._parse_prompt()


def test_missing_openai_key_is_reported(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(llm_engine.LLMError, match="OPENAI_API_KEY"):
        llm_engine.generate(JOB, CV, "m")
