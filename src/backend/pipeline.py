"""
    The tailoring and CV-parsing pipelines, independent of how they were triggered (an API
    request today; the discovery worker in later phases).
"""

import base64
import logging

from . import latex_guard, llm_engine, page_fit, render, resume_model
from .config import Settings
from .schemas import CVParseResult, Job, MasterCV, TailorResult

log = logging.getLogger("autoapplier.pipeline")


def tailor(job: Job, master_cv: MasterCV, settings: Settings) -> TailorResult:
    """
        Desc:
            LLM selection → selection check → render → LaTeX check → compile. The caller has
            already applied the criteria gate. Shared by POST /api/tailor and the worker.
        Raises:
            llm_engine.LLMError: model failure or an unusable selection.
            RuntimeError: unexpected server error (details are logged, not returned).
    """
    master_cv = resume_model.assign_ids(master_cv)
    try:
        llm = llm_engine.generate(job, master_cv, settings.openai_model)
    except llm_engine.LLMError:
        raise
    except Exception:
        log.exception("Unexpected error during LLM generation")
        raise RuntimeError("Unexpected server error during generation.")

    if not llm.is_qualified:
        return TailorResult(
            stage="llm", is_qualified=False,
            reason="The model judged the CV not qualified for this posting.",
            gap_analysis=llm.gap_analysis,
        )

    sections, notes = resume_model.check_selection(master_cv, llm)
    if not any(s.kind != "education" for s in sections):
        raise llm_engine.LLMError("The model selected no usable CV content for this resume.")
    latex = render.render_resume(master_cv.contact, sections)

    # The renderer escapes all CV text, so this is a backstop rather than the main defense.
    problems = latex_guard.check_latex(latex)
    if problems:
        return TailorResult(
            stage="compile", is_qualified=True,
            reason="The rendered LaTeX failed validation and was not compiled.",
            gap_analysis=llm.gap_analysis, latex_source=latex,
            compile_log="\n".join(problems), selection_notes=notes,
        )

    fit = page_fit.compile_one_page(latex, timeout=settings.compile_timeout_seconds)
    if fit["pdf"] is None:
        return TailorResult(
            stage="compile", is_qualified=True,
            reason="pdflatex could not compile the generated resume. Fix the LaTeX source by hand.",
            gap_analysis=llm.gap_analysis, latex_source=latex, compile_log=fit["log"],
            selection_notes=notes,
        )

    pages = fit["pages"] or 0
    if pages > 1:
        reason = (f"Resume generated, but it is {pages} pages even with the tightest layout. "
                  "Trim it by hand in the .tex.")
    elif fit["adjustment"]:
        reason = f"Resume generated. Layout tightened to fit one page ({fit['adjustment']})."
    else:
        reason = "Resume generated."

    return TailorResult(
        stage="complete", is_qualified=True, reason=reason,
        gap_analysis=llm.gap_analysis, latex_source=fit["latex"], page_count=pages,
        resume_pdf_base64=base64.b64encode(fit["pdf"]).decode("ascii"), selection_notes=notes,
    )


def parse(resume_text: str, settings: Settings) -> CVParseResult:
    """Splits CV text into typed sections and flags lines that don't match the text."""
    try:
        master_cv = llm_engine.parse_cv(resume_text, settings.openai_model)
    except llm_engine.LLMError:
        raise
    except Exception:
        log.exception("Unexpected error during CV parsing")
        raise RuntimeError("Unexpected server error while parsing the CV.")
    return CVParseResult(master_cv=master_cv,
                         unmatched_bullets=resume_model.unmatched_bullets(master_cv, resume_text))
