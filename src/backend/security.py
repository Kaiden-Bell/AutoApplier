"""
    Request authentication (per-account API tokens) and an in-process rate limiter.

    Public launch replaces operator-issued tokens with Google sessions and the in-process limiter
    with durable, shared limits (DESIGN.md §8.3, §8.6).
"""

import logging
import threading
import time
from collections import defaultdict, deque

from fastapi import Header, HTTPException, Request

from . import accounts

log = logging.getLogger(__name__)


class RateLimiter:
    """Sliding-window limiter: at most `limit` hits per `window` seconds per key."""

    def __init__(self, limit, window=60.0):
        self.limit = limit
        self.window = window
        self._hits = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key):
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] >= self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return False
            hits.append(now)
            return True

    def reset(self):
        with self._lock:
            self._hits.clear()


def make_account_dependency(database):
    """
        FastAPI dependency: resolves X-AutoApplier-Key to an active account (accounts.AccountInfo).
        Fails closed with 503 when no database is configured.
    """
    def require_account(x_autoapplier_key: str = Header(default="")):
        if not database.configured:
            raise HTTPException(503, "Server has no AUTOAPPLIER_DATABASE_URL configured.")
        try:
            with database.session() as session:
                account = accounts.find_by_token(session, x_autoapplier_key)
        except Exception:
            log.exception("Account lookup failed")
            raise HTTPException(503, "The account database is unavailable.")
        if account is None:
            raise HTTPException(401, "Missing or invalid X-AutoApplier-Key account token.")
        return account
    return require_account


def client_ip(request: Request):
    # Forwarded headers are deliberately ignored until a trusted proxy is configured.
    return request.client.host if request.client else "unknown"
