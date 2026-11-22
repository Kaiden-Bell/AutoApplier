"""Rendering selected CV sections to LaTeX, across different CV layouts."""

import shutil

import pytest

from backend import latex_guard, page_fit, render, resume_model
from backend.schemas import EntryPick, SectionPick, TailoredResume
from cv_fixtures import coursework_cv, experience_only_cv, projects_and_experience_cv


def select_all(cv):
    """A selection of everything, in CV order."""
    return TailoredResume(is_qualified=True, gap_analysis="", sections=[
        SectionPick(section_id=s.id, entries=[
            EntryPick(entry_id=e.id, bullets=[{"bullet_id": b.id, "rewrite": None} for b in e.bullets])
            for e in s.entries
        ]) for s in cv.sections
    ])


def render_all(cv):
    sections, notes = resume_model.check_selection(cv, select_all(cv))
    assert notes == []
    return render.render_resume(cv.contact, sections)


ALL_CVS = [projects_and_experience_cv, experience_only_cv, coursework_cv]


@pytest.mark.parametrize("make_cv", ALL_CVS)
def test_rendered_latex_passes_the_guard(make_cv):
    assert latex_guard.check_latex(render_all(make_cv())) == []


@pytest.mark.parametrize("make_cv", ALL_CVS)
def test_headings_are_the_users_own_in_order(make_cv):
    cv = make_cv()
    latex = render_all(cv)
    positions = [latex.index(f"\\section*{{{render.latex_escape(s.heading)}}}") for s in cv.sections]
    assert positions == sorted(positions)


def test_entries_render_under_their_own_sections():
    latex = render_all(projects_and_experience_cv())
    projects = latex.index("\\section*{Engineering Projects}")
    experience = latex.index("\\section*{Professional Experience}")
    freelance = latex.index("Freelance Web \\& Systems Developer")
    assert projects < experience < freelance


def test_contact_comes_from_the_cv():
    latex = render_all(projects_and_experience_cv())
    assert "\\textbf{Test Candidate}" in latex
    assert "test@example.com" in latex and "github.com/test-candidate" in latex


def test_list_kinds_render_as_label_lines():
    latex = render_all(coursework_cv())
    assert "\\textbf{Mathematics:} Linear Algebra, Probability \\& Statistics\\par" in latex
    assert "\\textbf{Languages:} Python, R\\par" in latex


@pytest.mark.parametrize("raw, expected", [
    ("R&D 50% $5 #1 a_b {x}", r"R\&D 50\% \$5 \#1 a\_b \{x\}"),
    ("back\\slash ~ ^", r"back\textbackslash{}slash \textasciitilde{} \textasciicircum{}"),
    ("4×4 keypad → UART", r"4$\times$4 keypad $\rightarrow$ UART"),
    ("Aug 2023 – May 2027 — “quoted” café", "Aug 2023 – May 2027 — “quoted” café"),
    ("日本語 Japanese 🚀", " Japanese "),
    ("\\write18{rm -rf}", r"\textbackslash{}write18\{rm -rf\}"),
])
def test_latex_escape(raw, expected):
    assert render.latex_escape(raw) == expected


def test_injected_latex_in_cv_text_is_neutralized():
    cv = projects_and_experience_cv()
    cv.sections[2].entries[0].bullets[0].text = "\\input{|ls} \\end{document} \\immediate\\write18{x}"
    latex = render_all(cv)
    assert latex_guard.check_latex(latex) == []
    assert latex.count("\\end{document}") == 1


@pytest.mark.skipif(shutil.which("pdflatex") is None, reason="pdflatex not installed")
@pytest.mark.parametrize("make_cv", ALL_CVS)
def test_renders_compile_to_one_page(make_cv):
    fit = page_fit.compile_one_page(render_all(make_cv()))
    assert fit["pdf"] is not None, fit["log"][-2000:]
    assert fit["pages"] == 1


@pytest.mark.skipif(shutil.which("pdflatex") is None, reason="pdflatex not installed")
def test_every_escaped_character_compiles():
    cv = projects_and_experience_cv()
    cv.sections[2].entries[0].bullets[0].text = (
        "".join(render._ESCAPES) + " café naïve “quoted” ‘single’ – — 日本語 🚀 end")
    fit = page_fit.compile_one_page(render_all(cv))
    assert fit["pdf"] is not None, fit["log"][-2000:]
