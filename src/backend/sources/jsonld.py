"""
    schema.org JobPosting extraction from a page's <script type="application/ld+json"> blocks.

    A Python port of extension/jsonld.js (same field mapping and pay format), used server-side to
    describe postings that come from career sites without a supported ATS API.
"""

import json
import re
from html.parser import HTMLParser

from .base import MAX_DESCRIPTION, format_pay, html_to_text

_UNITS = {"HOUR": "hour", "DAY": "day", "WEEK": "week", "MONTH": "month", "YEAR": "year"}


def _as_list(value):
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def is_job_posting(node):
    return isinstance(node, dict) and any(
        re.search(r"(^|[:/])JobPosting$", str(t)) for t in _as_list(node.get("@type")))


def find_job_posting(data, depth=0):
    """Depth-first search through arrays, @graph containers and nested objects."""
    if depth > 8 or not isinstance(data, (dict, list)):
        return None
    if isinstance(data, list):
        for item in data:
            found = find_job_posting(item, depth + 1)
            if found:
                return found
        return None
    if is_job_posting(data):
        return data
    for child in data.values():
        if isinstance(child, (dict, list)):
            found = find_job_posting(child, depth + 1)
            if found:
                return found
    return None


def format_location(posting):
    parts = []
    for loc in _as_list(posting.get("jobLocation")):
        addr = loc.get("address") if isinstance(loc, dict) and "address" in loc else loc
        if isinstance(addr, str):
            if addr.strip():
                parts.append(addr.strip())
        elif isinstance(addr, dict):
            country = addr.get("addressCountry")
            if isinstance(country, dict):
                country = country.get("name")
            line = ", ".join(x.strip() for x in (addr.get("addressLocality"), addr.get("addressRegion"), country)
                             if isinstance(x, str) and x.strip())
            if line:
                parts.append(line)
    if any(re.search(r"telecommute|remote", str(t), re.IGNORECASE)
           for t in _as_list(posting.get("jobLocationType"))):
        parts.insert(0, "Remote")
    return "; ".join(dict.fromkeys(parts))


def format_salary(posting):
    salary = posting.get("baseSalary", posting.get("estimatedSalary"))
    salary = (_as_list(salary) or [None])[0]
    if salary in (None, ""):
        return ""
    if not isinstance(salary, dict):
        return str(salary)
    currency = salary.get("currency") or salary.get("salaryCurrency") or "USD"
    value = salary.get("value", salary)
    unit = salary.get("unitText") or ""
    if isinstance(value, dict):
        low = value.get("minValue", value.get("value"))
        high = value.get("maxValue")
        unit = value.get("unitText") or unit
    else:
        low, high = value, None
    try:
        low = float(low) if low is not None else None
        high = float(high) if high is not None else None
    except (TypeError, ValueError):
        return ""
    unit_word = _UNITS.get(str(unit).upper(), str(unit).lower())
    return format_pay(low, high, currency, unit_word)


def parse_job_posting(posting):
    """Maps a JobPosting object to {title, location, pay, description, url}, or None."""
    if not is_job_posting(posting):
        return None
    org = posting.get("hiringOrganization")
    company = html_to_text(org.get("name") if isinstance(org, dict) else org)
    description = html_to_text(posting.get("description"))
    if company and company not in description:
        description = f"Company: {company}\n\n{description}"
    return {
        "title": html_to_text(posting.get("title") or posting.get("name") or ""),
        "company": company,
        "location": format_location(posting),
        "pay": format_salary(posting),
        "description": description[:MAX_DESCRIPTION],
        "url": posting.get("url") if isinstance(posting.get("url"), str) else "",
    }


class _LdJsonCollector(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks = []
        self._buffer = None

    def handle_starttag(self, tag, attrs):
        if tag == "script" and (dict(attrs).get("type") or "").lower() == "application/ld+json":
            self._buffer = []

    def handle_data(self, data):
        if self._buffer is not None:
            self._buffer.append(data)

    def handle_endtag(self, tag):
        if tag == "script" and self._buffer is not None:
            self.blocks.append("".join(self._buffer))
            self._buffer = None


def extract_from_html(page_html):
    """The first JobPosting found in the page's JSON-LD blocks, parsed, or None."""
    collector = _LdJsonCollector()
    try:
        collector.feed(page_html)
    except Exception:  # malformed markup: use whatever was collected
        pass
    for block in collector.blocks:
        try:
            data = json.loads(block)
        except ValueError:
            continue
        job = parse_job_posting(find_job_posting(data))
        if job and (job["title"] or job["description"]):
            return job
    return None
