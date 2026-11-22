"""
    Lever public Postings API (no key): https://github.com/lever/postings-api

    Board feed:  GET api.lever.co/v0/postings/{slug}?mode=json
    One job:     GET api.lever.co/v0/postings/{slug}/{id}
"""

import re
from urllib.parse import urlsplit

from .base import (
    FetchResult, NormalizedPosting, clean, format_pay, html_to_text, join_unique, parse_datetime,
)

KIND = "lever"
API = "https://api.lever.co/v0/postings"
_SLUG = r"[A-Za-z0-9_.-]+"
_ID = r"[0-9a-fA-F-]{36}"
_UNITS = {"per-year-salary": "year", "per-month-salary": "month", "per-week-salary": "week",
          "per-day-wage": "day", "per-hour-wage": "hour"}


def parse_url(url):
    parts = urlsplit(url)
    host = parts.netloc.lower()
    segments = [s for s in parts.path.split("/") if s]
    if host == "api.lever.co" and segments[:2] == ["v0", "postings"]:
        segments = segments[2:]
    elif host not in ("jobs.lever.co", "jobs.eu.lever.co"):
        return None
    if not segments or not re.fullmatch(_SLUG, segments[0]):
        return None
    job = segments[1] if len(segments) >= 2 and re.fullmatch(_ID, segments[1]) else None
    return segments[0], job


def check(http, slug):
    http.get(f"{API}/{slug}?mode=json&limit=1")
    return slug


def fetch(http, slug, etag=None, last_modified=None):
    resp = http.get(f"{API}/{slug}?mode=json", etag, last_modified)
    if resp.status == 304:
        return FetchResult([], etag, last_modified, not_modified=True)
    data = resp.json()
    if not isinstance(data, list):
        data = []
    return FetchResult([_posting(slug, p) for p in data if p.get("id")],
                       resp.headers.get("etag"), resp.headers.get("last-modified"))


def fetch_one(http, slug, job_id):
    return _posting(slug, http.get(f"{API}/{slug}/{job_id}").json())


def _posting(slug, p):
    categories = p.get("categories") or {}
    locations = categories.get("allLocations") or [categories.get("location")]
    if (p.get("workplaceType") or "").lower() == "remote":
        locations = ["Remote", *locations]
    sections = [p.get("descriptionPlain") or html_to_text(p.get("description"))]
    for block in p.get("lists") or []:
        items = html_to_text(block.get("content"))
        sections.append(f"{clean(block.get('text'))}\n{items}".strip())
    sections.append(p.get("additionalPlain") or html_to_text(p.get("additional")))
    return NormalizedPosting(
        external_id=str(p["id"]),
        title=clean(p.get("text")),
        company=slug,
        location=join_unique(locations),
        pay=_pay(p.get("salaryRange")),
        description=html_to_text("\n\n".join(s for s in sections if s)),
        canonical_url=p.get("hostedUrl") or f"https://jobs.lever.co/{slug}/{p['id']}",
        apply_url=p.get("applyUrl") or f"https://jobs.lever.co/{slug}/{p['id']}/apply",
        posted_at=parse_datetime(p.get("createdAt")),
        extra={"commitment": categories.get("commitment"), "team": categories.get("team")},
    )


def _pay(salary):
    if not salary:
        return ""
    return format_pay(salary.get("min"), salary.get("max"), salary.get("currency") or "USD",
                      _UNITS.get(salary.get("interval") or "", ""))
