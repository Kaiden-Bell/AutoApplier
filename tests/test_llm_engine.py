"""LLM engine error-path tests using a fake OpenAI client (no network)."""

from types import SimpleNamespace

import httpx
import openai
import pytest

from backend import llm_engine
from backend.schemas import Job, LLMResult

JOB = Job(title="Software Engineer", location="Reno, NV", pay="$40/hr", description="Build APIs.")


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
    result = LLMResult(is_qualified=True, gap_analysis="ok", latex_code="\\documentclass{article}")
    captured = fake_client(monkeypatch, message=msg(parsed=result))
    assert llm_engine.generate(JOB, "my cv", "test-model") == result
    assert captured["model"] == "test-model"
    assert captured["response_format"] is LLMResult
    user = captured["messages"][1]["content"]
    assert "Build APIs." in user and "my cv" in user and "Reno, NV" in user


def test_refusal(monkeypatch):
    fake_client(monkeypatch, message=msg(refusal="I can't help with that."))
    with pytest.raises(llm_engine.LLMError, match="refused"):
        llm_engine.generate(JOB, "cv", "m")


def test_unparseable_output(monkeypatch):
    fake_client(monkeypatch, message=msg(parsed=None))
    with pytest.raises(llm_engine.LLMError, match="expected format"):
        llm_engine.generate(JOB, "cv", "m")


def test_no_choices(monkeypatch):
    fake_client(monkeypatch, choices=False)
    with pytest.raises(llm_engine.LLMError, match="no choices"):
        llm_engine.generate(JOB, "cv", "m")


def test_upstream_api_failure(monkeypatch):
    err = openai.APIConnectionError(request=httpx.Request("POST", "https://api.openai.com"))
    fake_client(monkeypatch, raises=err)
    with pytest.raises(llm_engine.LLMError, match="APIConnectionError"):
        llm_engine.generate(JOB, "cv", "m")


def test_prompt_file_loads():
    assert "is_qualified" in llm_engine._system_prompt()


def test_missing_openai_key_is_reported(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(llm_engine.LLMError, match="OPENAI_API_KEY"):
        llm_engine.generate(JOB, "cv", "m")
