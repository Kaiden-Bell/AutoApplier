"""
    Shared types and text helpers for job-source adapters.
"""

import html
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

MAX_DESCRIPTION = 45_000


class SourceError(Exception):
    """A source could not be fetched or parsed."""


class SourceNotFound(SourceError):
    """The board or list does not exist (HTTP 404)."""


@dataclass
class NormalizedPosting:
    """One posting in the shape every adapter returns."""
    external_id: str
    title: str
    company: str
    location: str = ""
    pay: str = ""
    description: str = ""
    canonical_url: str = ""
    apply_url: str = ""
    posted_at: Optional[datetime] = None
    # False for postings the source reports as closed or hidden but still lists.
    active: bool = True
    extra: dict = field(default_factory=dict)


@dataclass
class FetchResult:
    postings: list[NormalizedPosting]
    etag: Optional[str] = None
    last_modified: Optional[str] = None
    not_modified: bool = False


# ---------- text ----------

_TAG_BREAK_RE = re.compile(r"<(br|/p|/h[1-6]|/div|/tr|/ul|/ol)\b[^>]*>", re.IGNORECASE)
_LI_RE = re.compile(r"<li\b[^>]*>", re.IGNORECASE)
_SCRIPT_RE = re.compile(r"<(script|style)\b.*?</\1>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_HAS_MARKUP_RE = re.compile(r"<[a-z!/][^>]*>|&[#a-z0-9]+;", re.IGNORECASE)


def html_to_text(value, limit=MAX_DESCRIPTION):
    """
        Desc:
            Converts a (possibly entity-encoded) HTML fragment to plain text with line breaks.
            Mirrors aaHtmlToText in extension/jsonld.js: two passes handle Greenhouse's
            entity-encoded HTML.
    """
    text = str(value or "")
    for _ in range(2):
        if not _HAS_MARKUP_RE.search(text):
            break
        text = _TAG_BREAK_RE.sub("\n", text)
        text = _LI_RE.sub("\n- ", text)
        text = _SCRIPT_RE.sub("", text)
        text = _TAG_RE.sub("", text)
        text = html.unescape(text)
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()[:limit]


def clean(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def join_unique(parts, sep="; "):
    seen = []
    for part in parts:
        part = clean(part)
        if part and part not in seen:
            seen.append(part)
    return sep.join(seen)


def format_amount(value, currency):
    symbol = "$" if (currency or "USD").upper() == "USD" else f"{currency.upper()} "
    number = float(value)
    shown = f"{number:,.2f}".rstrip("0").rstrip(".")
    return symbol + shown


def format_pay(minimum, maximum, currency, unit):
    """"$85,000 - $105,000 / year" — the shape criteria_matcher.parse_pay reads."""
    values = [v for v in (minimum, maximum) if v is not None]
    if not values:
        return ""
    low = format_amount(values[0], currency)
    high = format_amount(values[-1], currency)
    shown = low if low == high else f"{low} - {high}"
    return f"{shown} / {unit}" if unit else shown


def parse_datetime(value):
    """ISO-8601 string or epoch seconds/milliseconds → aware datetime, else None."""
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)):
            seconds = value / 1000 if value > 10_000_000_000 else value
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None


# ---------- URLs ----------

_TRACKING_PARAMS = {"ref", "source", "src", "gh_src", "lever-source", "lever-origin"}


def normalize_url(url):
    """
        Desc:
            A comparison key for job URLs: lowercase host without "www.", no fragment, no
            trailing slash, and no tracking parameters (utm_*, ref, source, ...). Used to spot the
            same job reported by two sources (for example Simplify and Greenhouse).
    """
    if not url:
        return ""
    parts = urlsplit(url.strip())
    host = parts.netloc.lower().removeprefix("www.")
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() not in _TRACKING_PARAMS]
    path = parts.path.rstrip("/") or "/"
    return urlunsplit(("https", host, path, urlencode(sorted(query)), ""))
