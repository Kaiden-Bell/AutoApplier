"""
    main.py - FastAPI backend for the AutoApplier extension.

    Run from the repository root:
        PYTHONPATH=src uvicorn backend.main:app --port 2005 --workers 1
"""

import hashlib
import logging
import os
import shutil
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware

from . import accounts, crypto, discovery, pipeline, sources
from .config import Settings, load_default_criteria
from .criteria_matcher import eval_job_criteria
from .db import Database
from .jobs import IdempotencyConflict, JobStore
from .schemas import (
    AccountOut, AddSourceRequest, CVParseRequest, PostingOut, ProfileOut, ProfileUpdate,
    RequestStatus, SourceOut, TailorRequest, TailorResult,
)
from .sources.http import HttpClient
from .security import RateLimiter, make_account_dependency

log = logging.getLogger("autoapplier")


def _fingerprint(req):
    return hashlib.sha256(req.model_dump_json().encode("utf-8")).hexdigest()


def create_app(settings: Optional[Settings] = None, database: Optional[Database] = None,
               http: Optional[HttpClient] = None) -> FastAPI:
    settings = settings or Settings()
    database = database or Database(settings.database_url)
    # Used only to check that a board exists when it is added to a watchlist.
    http = http or HttpClient(timeout=8.0, requests_per_minute=settings.host_requests_per_minute)
    store = JobStore(settings.worker_count, settings.job_ttl_seconds)
    limiter = RateLimiter(settings.rate_limit_per_minute)
    require_account = make_account_dependency(database)

    @asynccontextmanager
    async def lifespan(_app):
        if not database.configured:
            log.warning("AUTOAPPLIER_DATABASE_URL is not set; all /api requests will be refused.")
        yield
        store.shutdown()
        http.close()
        database.dispose()

    app = FastAPI(title="AutoApplier API", version="0.2.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.database = database
    app.state.store = store
    app.state.limiter = limiter

    # Extension service-worker fetches are not subject to CORS; this is defense in depth only.
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"^chrome-extension://[a-p]{32}$",
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "X-AutoApplier-Key", "Idempotency-Key"],
    )

    def data_key():
        try:
            return crypto.load_key(settings.data_key)
        except crypto.DataKeyError as e:
            raise HTTPException(503, str(e))

    def submit(account, fn, idempotency_key, fingerprint):
        try:
            request_id, _ = store.submit(fn, idempotency_key=idempotency_key,
                                         fingerprint=fingerprint, owner=account.id)
        except IdempotencyConflict:
            raise HTTPException(409, "Idempotency-Key was already used with different input.")
        return store.get(request_id, owner=account.id) or RequestStatus(request_id=request_id,
                                                                         status="running")

    @app.get("/health")
    def health():
        return {
            "ok": True,
            "pdflatex_available": shutil.which("pdflatex") is not None,
            "database_ok": database.ping(),
            "data_key_configured": bool(settings.data_key),
            "openai_key_configured": bool(os.environ.get("OPENAI_API_KEY")),
        }

    @app.get("/api/me")
    def me(account: accounts.AccountInfo = Depends(require_account)) -> AccountOut:
        return AccountOut(id=account.id, label=account.label, daily_llm_cap=account.daily_llm_cap,
                          automation_enabled=account.automation_enabled)

    @app.get("/api/profile")
    def get_profile(account: accounts.AccountInfo = Depends(require_account)) -> ProfileOut:
        key = data_key()
        try:
            with database.session() as session:
                return accounts.get_profile(session, key, account.id)
        except crypto.DataKeyError as e:
            raise HTTPException(500, str(e))

    @app.patch("/api/profile")
    def patch_profile(update: ProfileUpdate,
                      account: accounts.AccountInfo = Depends(require_account)) -> ProfileOut:
        key = data_key()
        try:
            with database.session() as session:
                return accounts.update_profile(session, key, account.id, update)
        except crypto.DataKeyError as e:
            raise HTTPException(500, str(e))

    @app.get("/api/sources")
    def list_sources(account: accounts.AccountInfo = Depends(require_account)) -> list[SourceOut]:
        with database.session() as session:
            return [SourceOut(**vars(v)) for v in discovery.list_sources(session, account.id)]

    @app.post("/api/sources", status_code=201)
    def add_source(req: AddSourceRequest,
                   account: accounts.AccountInfo = Depends(require_account)) -> SourceOut:
        try:
            with database.session() as session:
                source_id = discovery.add_source(session, http, account.id, req.url,
                                                 settings.poll_interval_seconds)
        except discovery.WatchlistError as e:
            raise HTTPException(422, str(e))
        except sources.SourceError as e:
            raise HTTPException(502, f"Couldn't reach that job board to check it ({e}). Try again.")
        with database.session() as session:
            views = discovery.list_sources(session, account.id)
        return SourceOut(**vars(next(v for v in views if v.id == source_id)))

    @app.delete("/api/sources/{source_id}", status_code=204)
    def remove_source(source_id: int,
                      account: accounts.AccountInfo = Depends(require_account)) -> Response:
        with database.session() as session:
            if not discovery.remove_source(session, account.id, source_id):
                raise HTTPException(404, "That source isn't on your watchlist.")
        return Response(status_code=204)

    @app.get("/api/postings")
    def recent_postings(limit: int = 50,
                        account: accounts.AccountInfo = Depends(require_account)) -> list[PostingOut]:
        limit = max(1, min(limit, 200))
        with database.session() as session:
            rows = discovery.recent_postings(session, account.id, limit)
            return [PostingOut.model_validate(r, from_attributes=True) for r in rows]

    @app.post(
        "/api/tailor",
        responses={202: {"model": RequestStatus}, 200: {"model": RequestStatus}},
    )
    def tailor(
        req: TailorRequest, response: Response,
        account: accounts.AccountInfo = Depends(require_account),
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

        if not limiter.allow(account.id):
            raise HTTPException(429, "Too many generation requests; try again in a minute.")

        status = submit(account, lambda: pipeline.tailor(req.job, req.master_cv, settings),
                        idempotency_key, _fingerprint(req))
        response.status_code = 202
        return status

    @app.post(
        "/api/cv/parse",
        responses={202: {"model": RequestStatus}, 200: {"model": RequestStatus}},
    )
    def parse_cv(
        req: CVParseRequest, response: Response,
        account: accounts.AccountInfo = Depends(require_account),
        idempotency_key: Optional[str] = Header(default=None, max_length=200),
    ) -> RequestStatus:
        if not limiter.allow(account.id):
            raise HTTPException(429, "Too many generation requests; try again in a minute.")
        status = submit(account, lambda: pipeline.parse(req.resume_text, settings),
                        idempotency_key, "parse:" + _fingerprint(req))
        response.status_code = 202
        return status

    @app.get("/api/requests/{request_id}")
    def get_request(request_id: str,
                    account: accounts.AccountInfo = Depends(require_account)) -> RequestStatus:
        status = store.get(request_id, owner=account.id)
        if status is None:
            raise HTTPException(404, "Unknown or expired request.")
        return status

    return app


app = create_app()
