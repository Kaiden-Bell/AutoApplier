"""
    Job-source adapters. Each module exposes KIND, parse_url(url), check(http, slug),
    fetch(http, slug, etag, last_modified) -> FetchResult and, for ATS boards, fetch_one(http,
    slug, job_id) -> NormalizedPosting.
"""

from . import ashby, greenhouse, lever, simplify
from .base import FetchResult, NormalizedPosting, SourceError, SourceNotFound  # noqa: F401

ADAPTERS = {m.KIND: m for m in (greenhouse, lever, ashby, simplify)}
# ATS boards whose single postings can be fetched for enrichment.
ATS_KINDS = ("greenhouse", "lever", "ashby")


def detect(url):
    """
        Desc:
            Identifies a supported board or list from a URL the user pasted (board page, job page,
            embed or API URL).
        Returns:
            tuple | None: (kind, slug, job_id or None).
    """
    url = (url or "").strip()
    if not url:
        return None
    if "://" not in url:
        url = "https://" + url
    for kind, adapter in ADAPTERS.items():
        found = adapter.parse_url(url)
        if found:
            return kind, found[0], found[1]
    return None
