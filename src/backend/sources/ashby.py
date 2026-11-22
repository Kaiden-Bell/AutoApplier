"""
    Ashby public Job Posting API (no key): https://developers.ashbyhq.com/docs/public-job-posting-api

    Board feed:  GET api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true
    There is no public single-job endpoint, so fetch_one reads the board and picks the job.
"""

import re
from urllib.parse import urlsplit

from .base import (
    FetchResult, NormalizedPosting, SourceNotFound, clean, html_to_text, join_unique,
    parse_datetime,
)

KIND = "ashby"
API = "https://api.ashbyhq.com/posting-api/job-board"
_SLUG = r"[A-Za-z0-9_.%-]+"
_ID = r"[0-9a-fA-F-]{36}"


def parse_url(url):
    parts = urlsplit(url)
    host = parts.netloc.lower()
    segments = [s for s in parts.path.split("/") if s]
    if host == "api.ashbyhq.com" and segments[:2] == ["posting-api", "job-board"]:
        segments = segments[2:]
    elif host != "jobs.ashbyhq.com":
        return None
    if not segments or not re.fullmatch(_SLUG, segments[0]):
        return None
    job = segments[1] if len(segments) >= 2 and re.fullmatch(_ID, segments[1]) else None
    return segments[0], job


def check(http, slug):
    http.get(f"{API}/{slug}")
    return slug


def fetch(http, slug, etag=None, last_modified=None):
    resp = http.get(f"{API}/{slug}?includeCompensation=true", etag, last_modified)
    if resp.status == 304:
        return FetchResult([], etag, last_modified, not_modified=True)
    jobs = resp.json().get("jobs") or []
    return FetchResult([_posting(slug, j) for j in jobs if j.get("id")],
                       resp.headers.get("etag"), resp.headers.get("last-modified"))


def fetch_one(http, slug, job_id):
    for posting in fetch(http, slug).postings:
        if posting.external_id == job_id:
            return posting
    raise SourceNotFound(f"Ashby job {job_id} is not on the {slug} board")


def _posting(slug, job):
    locations = [job.get("location")] + [s.get("location") for s in job.get("secondaryLocations") or []]
    if job.get("isRemote") and not any("remote" in (loc or "").lower() for loc in locations):
        locations.insert(0, "Remote")
    compensation = job.get("compensation") or {}
    pay = (compensation.get("scrapeableCompensationSalarySummary")
           or compensation.get("compensationTierSummary") or "")
    job_id = str(job["id"])
    return NormalizedPosting(
        external_id=job_id,
        title=clean(job.get("title")),
        company=slug,
        location=join_unique(locations),
        pay=clean(pay),
        description=job.get("descriptionPlain") or html_to_text(job.get("descriptionHtml")),
        canonical_url=job.get("jobUrl") or f"https://jobs.ashbyhq.com/{slug}/{job_id}",
        apply_url=job.get("applyUrl") or f"https://jobs.ashbyhq.com/{slug}/{job_id}/application",
        posted_at=parse_datetime(job.get("publishedAt")),
        active=job.get("isListed", True) is not False,
        extra={"employment_type": job.get("employmentType"), "department": job.get("department")},
    )
