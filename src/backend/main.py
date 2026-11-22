"""
    main.py - FastAPI proxy backend for the AutoApplier extension.

    Run from the repository root:
        PYTHONPATH=src uvicorn backend.main:app --port 2005 --workers 1
"""

import base64
import hashlib
import logging
import os
import shutil
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from . import latex_guard, llm_engine, page_fit, render, resume_model
from .config import Settings, load_default_criteria
from .criteria_matcher import eval_job_criteria
from .jobs import IdempotencyConflict, JobStore
from .schemas import CVParseRequest, CVParseResult, RequestStatus, TailorRequest, TailorResult
from .security import RateLimiter, client_ip, make_api_key_dependency

log = logging.getLogger("autoapplier")


def run_pipeline(req: TailorRequest, settings: Settings) -> TailorResult:
    """LLM selection → selection check → render → LaTeX check → compile. Criteria already passed."""
    master_cv = resume_model.assign_ids(req.master_cv)
    try:
        llm = llm_engine.generate(req.job, master_cv, settings.openai_model)
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


def run_parse(req: CVParseRequest, settings: Settings) -> CVParseResult:
    try:
        master_cv = llm_engine.parse_cv(req.resume_text, settings.openai_model)
    except llm_engine.LLMError:
        raise
    except Exception:
        log.exception("Unexpected error during CV parsing")
        raise RuntimeError("Unexpected server error while parsing the CV.")
    return CVParseResult(master_cv=master_cv,
                         unmatched_bullets=resume_model.unmatched_bullets(master_cv, req.resume_text))


def _fingerprint(req):
    return hashlib.sha256(req.model_dump_json().encode("utf-8")).hexdigest()


def create_app(settings: Optional[Settings] = None) -> FastAPI:
    settings = settings or Settings()
    store = JobStore(settings.worker_count, settings.job_ttl_seconds)
    limiter = RateLimiter(settings.rate_limit_per_minute)
    require_api_key = make_api_key_dependency(lambda: settings)

    @asynccontextmanager
    async def lifespan(_app):
        if not settings.api_key:
            log.warning("AUTOAPPLIER_API_KEY is not set; all /api requests will be refused.")
        yield
        store.shutdown()

    app = FastAPI(title="AutoApplier API", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.store = store
    app.state.limiter = limiter

    # Extension service-worker fetches are not subject to CORS; this is defense in depth only.
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^chrome-extension://[a-p]{32}$",
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-AutoApplier-Key", "Idempotency-Key"],
    )

    @app.get("/health")
    def health():
        return {
            "ok": True,
            "pdflatex_available": shutil.which("pdflatex") is not None,
            "api_key_configured": bool(settings.api_key),
            "openai_key_configured": bool(os.environ.get("OPENAI_API_KEY")),
        }

    @app.post(
        "/api/tailor",
        dependencies=[Depends(require_api_key)],
        responses={202: {"model": RequestStatus}, 200: {"model": RequestStatus}},
    )
    def tailor(
        req: TailorRequest, request: Request, response: Response,
        idempotency_key: Optional[str] = Header(default=None, max_length=200),
    ) -> RequestStatus:
        criteria = req.criteria.model_dump() if req.criteria else load_default_criteria()
        passed, reason = eval_job_criteria(req.job.model_dump(), criteria)
        if not passed:
            # No model call happens, so criteria rejections do not count toward the rate limit.
            return RequestStatus(
                request_id="", status="done",
                result=TailorResult(stage="criteria", is_qualified=False, reason=reason),
            )

        if not limiter.allow(client_ip(request)):
            raise HTTPException(429, "Too many generation requests; try again in a minute.")

        try:
            request_id, _ = store.submit(
                lambda: run_pipeline(req, settings),
                idempotency_key=idempotency_key, fingerprint=_fingerprint(req),
            )
        except IdempotencyConflict:
            raise HTTPException(409, "Idempotency-Key was already used with different input.")

        response.status_code = 202
        return store.get(request_id) or RequestStatus(request_id=request_id, status="running")

    @app.post(
        "/api/cv/parse",
        dependencies=[Depends(require_api_key)],
        responses={202: {"model": RequestStatus}, 200: {"model": RequestStatus}},
    )
    def parse_cv(
        req: CVParseRequest, request: Request, response: Response,
        idempotency_key: Optional[str] = Header(default=None, max_length=200),
    ) -> RequestStatus:
        if not limiter.allow(client_ip(request)):
            raise HTTPException(429, "Too many generation requests; try again in a minute.")
        try:
            request_id, _ = store.submit(
                lambda: run_parse(req, settings),
                idempotency_key=idempotency_key, fingerprint="parse:" + _fingerprint(req),
            )
        except IdempotencyConflict:
            raise HTTPException(409, "Idempotency-Key was already used with different input.")
        response.status_code = 202
        return store.get(request_id) or RequestStatus(request_id=request_id, status="running")

    @app.get("/api/requests/{request_id}", dependencies=[Depends(require_api_key)])
    def get_request(request_id: str) -> RequestStatus:
        status = store.get(request_id)
        if status is None:
            raise HTTPException(404, "Unknown or expired request.")
        return status

    return app


app = create_app()
