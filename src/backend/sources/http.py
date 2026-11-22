"""
    HTTP client for job sources: conditional requests, a generic User-Agent, timeouts, a response
    size cap, a per-host request budget, and per-host cooldowns when a server rate-limits us.

    Every board on one ATS shares a host (for example boards-api.greenhouse.io), so limits are
    tracked per host, not per board:
      - Budget: requests to one host are spaced at least 60 / requests_per_minute seconds apart
        (and never closer than MIN_HOST_INTERVAL).
      - Cooldown: a 429 (or a 503 with Retry-After) pauses the whole host until Retry-After has
        passed. Requests to a cooling host fail fast with RateLimited, without touching the network.
"""

import json
import threading
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit

import httpx

from .base import SourceError, SourceNotFound

USER_AGENT = "AutoApplier/0.2 (job-board polling)"
MAX_BYTES = 40 * 1024 * 1024        # Simplify's listings.json is ~13 MB
MIN_HOST_INTERVAL = 1.0             # seconds; Lever's robots.txt asks for a 1 s crawl delay
DEFAULT_RETRY_AFTER = 15 * 60       # seconds, when a 429 doesn't say how long to wait
MAX_RETRY_AFTER = 6 * 60 * 60


class RateLimited(SourceError):
    """The host asked us to slow down. `retry_after` is seconds until it may be tried again."""

    def __init__(self, message, retry_after):
        super().__init__(message)
        self.retry_after = retry_after


class Response:
    def __init__(self, status, body, headers):
        self.status = status
        self.body = body
        self.headers = headers

    def json(self):
        try:
            return json.loads(self.body)
        except ValueError as e:
            raise SourceError(f"Response was not valid JSON: {e}") from e

    @property
    def text(self):
        return self.body.decode("utf-8", errors="replace")


def parse_retry_after(value, now=None):
    """Seconds to wait from a Retry-After header (delta-seconds or HTTP date), capped."""
    if not value:
        return DEFAULT_RETRY_AFTER
    value = value.strip()
    if value.isdigit():
        seconds = int(value)
    else:
        try:
            when = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return DEFAULT_RETRY_AFTER
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        seconds = (when - (now or datetime.now(timezone.utc))).total_seconds()
    return max(1, min(int(seconds), MAX_RETRY_AFTER))


class HttpClient:
    """Thread-safe polite client. Pass `transport` (httpx.MockTransport) in tests."""

    def __init__(self, transport=None, timeout=30.0, requests_per_minute=30,
                 min_host_interval=MIN_HOST_INTERVAL, sleep=time.sleep, clock=time.monotonic):
        self._client = httpx.Client(
            transport=transport, timeout=timeout, follow_redirects=True,
            headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"},
        )
        budget = 60.0 / requests_per_minute if requests_per_minute else 0.0
        self._spacing = max(min_host_interval, budget)
        self._sleep = sleep
        self._clock = clock
        self._lock = threading.Lock()
        self._next_slot = {}       # host -> earliest clock time for its next request
        self._cooldown = {}        # host -> clock time when a rate limit ends

    def cooldown_remaining(self, url):
        """Seconds until the URL's host may be contacted again (0 if not cooling down)."""
        host = urlsplit(url).netloc.lower()
        with self._lock:
            return max(0.0, self._cooldown.get(host, 0.0) - self._clock())

    def _reserve_slot(self, host):
        with self._lock:
            now = self._clock()
            cooling = self._cooldown.get(host, 0.0) - now
            if cooling > 0:
                raise RateLimited(f"{host} is rate-limiting requests; waiting {int(cooling)}s",
                                  int(cooling) + 1)
            slot = max(now, self._next_slot.get(host, 0.0))
            self._next_slot[host] = slot + self._spacing
            return slot - now

    def _start_cooldown(self, host, seconds):
        with self._lock:
            until = self._clock() + seconds
            self._cooldown[host] = max(self._cooldown.get(host, 0.0), until)

    def get(self, url, etag=None, last_modified=None, method="GET"):
        """
            Desc:
                Fetches a URL. A 304 returns a Response with status 304 and no body.
            Raises:
                SourceNotFound: on 404.
                RateLimited: on 429 (or 503 with Retry-After), or while the host is cooling down.
                SourceError: on other HTTP errors, network errors, or oversized responses.
        """
        host = urlsplit(url).netloc.lower()
        delay = self._reserve_slot(host)
        if delay:
            self._sleep(delay)
        headers = {}
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified
        try:
            with self._client.stream(method, url, headers=headers) as resp:
                status = resp.status_code
                if status == 304:
                    return Response(304, b"", resp.headers)
                if status == 404:
                    raise SourceNotFound(f"{url} returned 404")
                retry_after = resp.headers.get("retry-after")
                if status == 429 or (status == 503 and retry_after):
                    seconds = parse_retry_after(retry_after)
                    self._start_cooldown(host, seconds)
                    raise RateLimited(f"{host} returned HTTP {status}; retrying in {seconds}s", seconds)
                if status >= 400:
                    raise SourceError(f"{url} returned HTTP {status}")
                chunks, size = [], 0
                for chunk in resp.iter_bytes():
                    size += len(chunk)
                    if size > MAX_BYTES:
                        raise SourceError(f"{url} is larger than {MAX_BYTES // (1024 * 1024)} MB")
                    chunks.append(chunk)
                return Response(status, b"".join(chunks), resp.headers)
        except httpx.HTTPError as e:
            raise SourceError(f"{url} failed: {type(e).__name__}") from e

    def close(self):
        self._client.close()
