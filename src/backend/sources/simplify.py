"""
    SimplifyJobs internship / new-grad lists on GitHub (for example SimplifyJobs/Summer2027-Internships).

    Feed: raw.githubusercontent.com/SimplifyJobs/{repo}/dev/.github/scripts/listings.json
    (~13 MB; GitHub answers If-None-Match with 304, so unchanged polls are cheap).

    Entries carry title, company, locations and the employer's own apply URL, but no description.
    Descriptions are fetched later, only for postings that pass a user's criteria (enrich.py).
"""

import re
from urllib.parse import urlsplit

from .base import FetchResult, NormalizedPosting, clean, join_unique, parse_datetime

KIND = "simplify"
RAW = "https://raw.githubusercontent.com/SimplifyJobs/{repo}/dev/.github/scripts/listings.json"
_REPO = r"[A-Za-z0-9_.-]+"


def parse_url(url):
    parts = urlsplit(url)
    segments = [s for s in parts.path.split("/") if s]
    if parts.netloc.lower() in ("github.com", "raw.githubusercontent.com") and len(segments) >= 2 \
            and segments[0].lower() == "simplifyjobs" and re.fullmatch(_REPO, segments[1]):
        return segments[1], None
    return None


def check(http, repo):
    http.get(RAW.format(repo=repo), method="HEAD")
    return f"Simplify {repo.replace('-', ' ')}"


def fetch(http, repo, etag=None, last_modified=None):
    resp = http.get(RAW.format(repo=repo), etag, last_modified)
    if resp.status == 304:
        return FetchResult([], etag, last_modified, not_modified=True)
    data = resp.json()
    if not isinstance(data, list):
        data = []
    postings = [_posting(item) for item in data if item.get("id") and item.get("url")]
    return FetchResult(postings, resp.headers.get("etag"), resp.headers.get("last-modified"))


def _posting(item):
    return NormalizedPosting(
        external_id=str(item["id"]),
        title=clean(item.get("title")),
        company=clean(item.get("company_name")),
        location=join_unique(item.get("locations") or []),
        canonical_url=item["url"],
        apply_url=item["url"],
        posted_at=parse_datetime(item.get("date_posted")),
        active=bool(item.get("active")) and item.get("is_visible", True) is not False,
        extra={k: item.get(k) for k in ("terms", "category", "sponsorship", "degrees")
               if item.get(k)},
    )
