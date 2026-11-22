"""
    Fills in a posting's description when its source didn't provide one (Simplify entries).

    Called only for postings that already passed a user's criteria, so discovery itself stays
    cheap. A URL on a supported ATS is fetched through that ATS's API; any other page is fetched
    once and read for schema.org JobPosting JSON-LD.
"""

from . import ADAPTERS, ATS_KINDS, detect, jsonld
from .base import SourceError, html_to_text


def fetch_details(http, url):
    """
        Desc:
            Looks up a posting's details from its URL.
        Returns:
            dict | None: {description, pay, location} with whatever was found, or None if the page
            has nothing usable.
        Raises:
            SourceError: the page or API could not be fetched.
    """
    found = detect(url)
    if found and found[0] in ATS_KINDS and found[2]:
        kind, slug, job_id = found
        posting = ADAPTERS[kind].fetch_one(http, slug, job_id)
        return {"description": posting.description, "pay": posting.pay, "location": posting.location}

    page = http.get(url).text
    job = jsonld.extract_from_html(page)
    if job and job["description"]:
        return {"description": job["description"], "pay": job["pay"], "location": job["location"]}
    return None


__all__ = ["fetch_details", "SourceError", "html_to_text"]
