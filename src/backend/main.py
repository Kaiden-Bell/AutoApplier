"""
    main.py - FastAPI proxy backend for the AutoApplier extension.

    Run from the repository root:
        PYTHONPATH=src uvicorn backend.main:app --port 8000 --workers 1
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

from . import compile_adapter, latex_guard, llm_engine
from .config import Settings, load_default_criteria
from .criteria_matcher import eval_job_criteria
from .jobs import IdempotencyConflict, JobStore
from .schemas import RequestStatus, TailorRequest, TailorResult
from .security import RateLimiter, client_ip, make_api_key_dependency

log = logging.getLogger("autoapplier")


def run_pipeline(req: TailorRequest, settings: Settings) -> TailorResult:
    """LLM → LaTeX validation → compile. The criteria gate has already passed."""
    try:
        llm = llm_engine.generate(req.job, req.resume_text, settings.openai_model)
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

    problems = latex_guard.check_latex(llm.latex_code)
    if problems:
        return TailorResult(
            stage="compile", is_qualified=True,
            reason="The generated LaTeX failed validation and was not compiled.",
            gap_analysis=llm.gap_analysis, latex_source=llm.latex_code,
            compile_log="\n".join(problems),
        )

    pdf_bytes, compile_log = compile_adapter.compile_latex_source(
        llm.latex_code, timeout=settings.compile_timeout_seconds,
    )
    if pdf_bytes is None:
        return TailorResult(
            stage="compile", is_qualified=True,
            reason="pdflatex could not compile the generated resume. Fix the LaTeX source by hand.",
            gap_analysis=llm.gap_analysis, latex_source=llm.latex_code, compile_log=compile_log,
        )

    return TailorResult(
        stage="complete", is_qualified=True, reason="Resume generated.",
        gap_analysis=llm.gap_analysis, latex_source=llm.latex_code,
        resume_pdf_base64=base64.b64encode(pdf_bytes).decode("ascii"),
    )


def _fingerprint(req: TailorRequest):
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

    @app.get("/api/requests/{request_id}", dependencies=[Depends(require_api_key)])
    def get_request(request_id: str) -> RequestStatus:
        status = store.get(request_id)
        if status is None:
            raise HTTPException(404, "Unknown or expired request.")
        return status

    return app


app = create_app()
