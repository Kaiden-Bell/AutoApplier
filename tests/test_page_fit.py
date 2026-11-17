"""One-page enforcement: logic with a fake compiler, plus real pdflatex when available."""

import shutil

import pytest

from backend import compile_adapter, page_fit
from latex_fixtures import resume

DOC = "\\documentclass{article}\n\\begin{document}\nHi\n\\end{document}\n"


def log_for(pages):
    return f"...\nOutput written on resume.pdf ({pages} page{'s' if pages > 1 else ''}, 1234 bytes).\n"


def test_count_pages():
    assert page_fit.count_pages(log_for(1)) == 1
    assert page_fit.count_pages(log_for(3)) == 3
    assert page_fit.count_pages("no output line") is None


def test_layout_is_injected_at_end_of_preamble_without_touching_content():
    out = page_fit.with_layout(DOC, "\\geometry{margin=0.5in}\n")
    preamble, body = out.split("\\begin{document}")
    assert "\\geometry{margin=0.5in}" in preamble
    assert body == DOC.split("\\begin{document}")[1]


@pytest.fixture
def fake_compile(monkeypatch):
    """Pages for the original document and for each layout level, in order."""
    state = {"pages": [], "calls": []}

    def fake(src, timeout=30):
        state["calls"].append(src)
        pages = state["pages"][len(state["calls"]) - 1]
        return (None, "! error") if pages is None else (b"%PDF", log_for(pages))

    monkeypatch.setattr(compile_adapter, "compile_latex_source", fake)
    return state


def test_one_page_document_is_left_alone(fake_compile):
    fake_compile["pages"] = [1]
    fit = page_fit.compile_one_page(DOC)
    assert fit["pages"] == 1 and fit["latex"] == DOC and fit["adjustment"] == ""
    assert len(fake_compile["calls"]) == 1


def test_escalates_until_it_fits(fake_compile):
    fake_compile["pages"] = [2, 2, 1]
    fit = page_fit.compile_one_page(DOC)
    assert fit["pages"] == 1
    assert fit["adjustment"] == page_fit._LAYOUT_LEVELS[1][0]
    assert fit["latex"] == fake_compile["calls"][2]


def test_skips_a_level_that_breaks_compilation(fake_compile):
    fake_compile["pages"] = [2, None, 1]
    fit = page_fit.compile_one_page(DOC)
    assert fit["pages"] == 1 and fit["adjustment"] == page_fit._LAYOUT_LEVELS[1][0]


def test_returns_tightest_result_when_nothing_fits(fake_compile):
    fake_compile["pages"] = [3, 3, 2, 2]
    fit = page_fit.compile_one_page(DOC)
    assert fit["pages"] == 2 and fit["pdf"] is not None
    assert fit["adjustment"] == page_fit._LAYOUT_LEVELS[-1][0]


requires_pdflatex = pytest.mark.skipif(shutil.which("pdflatex") is None, reason="pdflatex not installed")


@requires_pdflatex
def test_real_overflow_is_tightened_to_one_page():
    source = resume(20)  # two pages with the original layout
    _, log = compile_adapter.compile_latex_source(source, timeout=60)
    assert page_fit.count_pages(log) == 2
    fit = page_fit.compile_one_page(source, timeout=60)
    assert fit["pages"] == 1 and fit["pdf"].startswith(b"%PDF") and fit["adjustment"]


@requires_pdflatex
def test_real_document_too_long_to_fit_still_returns_pdf():
    fit = page_fit.compile_one_page(resume(40), timeout=60)
    assert fit["pdf"] is not None and fit["pages"] > 1
