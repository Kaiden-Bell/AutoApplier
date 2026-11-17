"""
    Private-development protection: a shared API key and an in-process per-IP rate limiter.

    Public launch replaces the shared key with per-user Google sessions and durable,
    shared limits (DESIGN.md §8.3, §8.6). This module is not sufficient for a public service.
"""

import secrets
import threading
import time
from collections import defaultdict, deque

from fastapi import Header, HTTPException, Request


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


def make_api_key_dependency(get_settings):
    def require_api_key(x_autoapplier_key: str = Header(default="")):
        expected = get_settings().api_key
        if not expected:
            raise HTTPException(503, "Server has no AUTOAPPLIER_API_KEY configured.")
        if not x_autoapplier_key or not secrets.compare_digest(x_autoapplier_key, expected):
            raise HTTPException(401, "Missing or invalid X-AutoApplier-Key.")
    return require_api_key


def client_ip(request: Request):
    # Forwarded headers are deliberately ignored until a trusted proxy is configured.
    return request.client.host if request.client else "unknown"
