"""
    In-memory store for background tailoring requests.

    A tailoring run (LLM + compile) can take longer than an MV3 service worker will wait on a
    single fetch, so POST /api/tailor queues the work and the extension polls for the result.
    State lives in this process only, so the API must run as a single worker.
"""

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

from .schemas import RequestStatus


class IdempotencyConflict(Exception):
    """An idempotency key was reused with different request input."""


class JobStore:
    def __init__(self, workers, ttl_seconds):
        self.ttl = ttl_seconds
        self._executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="tailor")
        self._lock = threading.Lock()
        self._jobs = {}          # request_id -> (created_at, owner, RequestStatus)
        self._by_key = {}        # (owner, idempotency key) -> (request_id, input fingerprint)

    def _purge(self, now):
        expired = [rid for rid, (created, _, _) in self._jobs.items() if now - created > self.ttl]
        for rid in expired:
            del self._jobs[rid]
        for key, (rid, _) in list(self._by_key.items()):
            if rid not in self._jobs:
                del self._by_key[key]

    def submit(self, fn, idempotency_key=None, fingerprint="", owner=None):
        """
            Runs fn() in the background and returns (request_id, created). fn returns a
            TailorResult or CVParseResult, or raises; an exception's message becomes the error string.
            Reusing an idempotency key returns the existing request without new work, or raises
            IdempotencyConflict if the input fingerprint differs. Keys and results are scoped to
            `owner` (the account ID), so accounts cannot see or collide with each other's requests.
        """
        now = time.monotonic()
        scoped_key = (owner, idempotency_key) if idempotency_key else None
        with self._lock:
            self._purge(now)
            if scoped_key and scoped_key in self._by_key:
                existing_id, existing_fp = self._by_key[scoped_key]
                if existing_fp != fingerprint:
                    raise IdempotencyConflict(idempotency_key)
                return existing_id, False
            request_id = uuid.uuid4().hex
            self._jobs[request_id] = (now, owner, RequestStatus(request_id=request_id, status="running"))
            if scoped_key:
                self._by_key[scoped_key] = (request_id, fingerprint)

        self._executor.submit(self._run, request_id, fn)
        return request_id, True

    def _run(self, request_id, fn):
        try:
            result = fn()
            update = {"status": "done", "result": result}
        except Exception as e:  # reported to the client as a failed request
            update = {"status": "error", "error": str(e) or type(e).__name__}
        with self._lock:
            entry = self._jobs.get(request_id)
            if entry:
                created, owner, status = entry
                self._jobs[request_id] = (created, owner, status.model_copy(update=update))

    def get(self, request_id, owner=None):
        """The request's status, or None if it is unknown, expired, or owned by someone else."""
        with self._lock:
            self._purge(time.monotonic())
            entry = self._jobs.get(request_id)
            if entry is None or entry[1] != owner:
                return None
            return entry[2]

    def shutdown(self):
        self._executor.shutdown(wait=False, cancel_futures=True)
