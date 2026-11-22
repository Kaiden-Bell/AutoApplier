"""
    Greenhouse public Job Board API (no key): https://developers.greenhouse.io/job-board.html

    Board feed:  GET boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true&pay_transparency=true
    One job:     GET boards-api.greenhouse.io/v1/boards/{slug}/jobs/{id}?pay_transparency=true
    Board name:  GET boards-api.greenhouse.io/v1/boards/{slug}
"""

import re
from urllib.parse import parse_qs, urlsplit

from .base import FetchResult, NormalizedPosting, clean, format_pay, html_to_text, parse_datetime

KIND = "greenhouse"
API = "https://boards-api.greenhouse.io/v1/boards"
_SLUG = r"[A-Za-z0-9_.-]+"
_HOSTS = {"boards.greenhouse.io", "job-boards.greenhouse.io", "job-boards.eu.greenhouse.io",
          "boards.eu.greenhouse.io"}


def parse_url(url):
    """(slug, job_id or None) for a Greenhouse board/job/embed/API URL, else None."""
    parts = urlsplit(url)
    host = parts.netloc.lower()
    segments = [s for s in parts.path.split("/") if s]
    if host == "boards-api.greenhouse.io":
        # /v1/boards/{slug}[/jobs[/{id}]]
        if len(segments) >= 3 and segments[:2] == ["v1", "boards"]:
            job = segments[4] if len(segments) >= 5 and segments[3] == "jobs" else None
            return _ok(segments[2], job)
        return None
    if host not in _HOSTS:
        return None
    query = parse_qs(parts.query)
    if segments[:1] == ["embed"]:
        slug = (query.get("for") or [""])[0]
        job = (query.get("token") or [None])[0]
        return _ok(slug, job)
    if not segments:
        return None
    job = segments[2] if len(segments) >= 3 and segments[1] == "jobs" else None
    return _ok(segments[0], job)


def _ok(slug, job):
    if not slug or not re.fullmatch(_SLUG, slug):
        return None
    if job is not None and not re.fullmatch(r"\d+", job):
        job = None
    return slug, job


def hosted_url(slug, job_id):
    return f"https://job-boards.greenhouse.io/{slug}/jobs/{job_id}"


def check(http, slug):
    """The board's display name. Raises SourceNotFound for an unknown board."""
    data = http.get(f"{API}/{slug}").json()
    return clean(data.get("name")) or slug


def fetch(http, slug, etag=None, last_modified=None):
    resp = http.get(f"{API}/{slug}/jobs?content=true&pay_transparency=true", etag, last_modified)
    if resp.status == 304:
        return FetchResult([], etag, last_modified, not_modified=True)
    jobs = resp.json().get("jobs") or []
    return FetchResult([_posting(slug, job) for job in jobs if job.get("id")],
                       resp.headers.get("etag"), resp.headers.get("last-modified"))


def fetch_one(http, slug, job_id):
    return _posting(slug, http.get(f"{API}/{slug}/jobs/{job_id}?pay_transparency=true").json())


def _posting(slug, job):
    job_id = str(job["id"])
    offices = [o.get("location") or o.get("name") for o in job.get("offices") or []]
    location = clean((job.get("location") or {}).get("name")) or "; ".join(filter(None, offices))
    return NormalizedPosting(
        external_id=job_id,
        title=clean(job.get("title")),
        company=clean(job.get("company_name")) or slug,
        location=location,
        pay=_pay(job.get("pay_input_ranges") or []),
        description=html_to_text(job.get("content")),
        canonical_url=job.get("absolute_url") or hosted_url(slug, job_id),
        apply_url=hosted_url(slug, job_id),
        posted_at=parse_datetime(job.get("first_published") or job.get("updated_at")),
    )


def _pay(ranges):
    """The first range, e.g. {"min_cents": 8500000, "max_cents": 10500000, "currency_type": "USD",
    "title": "US Annual Base Pay"} → "$85,000 - $105,000 / year"."""
    for r in ranges:
        if r.get("min_cents") is None and r.get("max_cents") is None:
            continue
        cents = [r.get("min_cents"), r.get("max_cents")]
        low, high = (c / 100 if c is not None else None for c in cents)
        unit = "hour" if re.search(r"hour", r.get("title") or "", re.IGNORECASE) else "year"
        return format_pay(low, high, r.get("currency_type") or "USD", unit)
    return ""
