"""
    Job discovery: the watchlist API's storage side, and polling sources for new postings.

    Each source (one Greenhouse/Lever/Ashby board or Simplify list) is polled once for every
    account watching it, so request volume grows with distinct boards, not users. Polls are
    conditional (ETag / Last-Modified) and spaced by the poll interval with jitter, stretched for
    sources that haven't changed in days (adaptive_interval). Failures back off exponentially; a
    rate limit (429 + Retry-After) defers the source until the host allows requests again, and the
    HTTP client pauses every other board on that host too (sources/http.py).

    The first successful poll of a source is a baseline: its postings are recorded but never
    matched. Postings that disappear from the feed are closed.

    Workers claim due sources with SELECT ... FOR UPDATE SKIP LOCKED plus a lease, so several
    worker processes can run without polling the same source twice.
"""

import hashlib
import json
import logging
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import exists, func, or_, select

from . import matching, sources
from .models import AccountSource, Posting, Source, utcnow
from .sources.base import normalize_url
from .sources.http import RateLimited

log = logging.getLogger("autoapplier.discovery")

LEASE = timedelta(minutes=10)
MAX_BACKOFF = timedelta(hours=6)
# (unchanged for at least, interval multiplier): quiet boards are checked less often.
QUIET_TIERS = ((timedelta(days=7), 4), (timedelta(days=2), 2))


class WatchlistError(ValueError):
    """A URL that can't be added to the watchlist; the message is shown to the user."""


# ---------- watchlist ----------

@dataclass
class SourceView:
    id: int
    kind: str
    slug: str
    name: str
    last_polled_at: datetime | None
    last_error: str | None
    baseline_done: bool
    open_postings: int
    new_postings_7d: int


def add_source(session, http, account_id, url, interval):
    """
        Desc:
            Adds the board or list behind `url` to the account's watchlist, creating the source
            if no one watches it yet (after checking it exists).
        Raises:
            WatchlistError: unsupported URL, or the board does not exist.
            sources.SourceError: the board couldn't be checked (network or server error).
    """
    found = sources.detect(url)
    if not found:
        raise WatchlistError("That URL isn't a Greenhouse, Lever or Ashby job board, or a "
                             "SimplifyJobs list on GitHub.")
    kind, slug, _job = found
    source = session.scalars(select(Source).where(Source.kind == kind, Source.slug == slug)).first()
    if source is None:
        try:
            name = sources.ADAPTERS[kind].check(http, slug)
        except sources.SourceNotFound as e:
            raise WatchlistError(f"No {kind.title()} board named “{slug}” was found.") from e
        source = Source(kind=kind, slug=slug, name=name, next_poll_at=utcnow())
        session.add(source)
        session.flush()
    elif not _has_subscribers(session, source.id) and _stale(source, interval):
        # Unwatched for a while: re-baseline so postings published meanwhile aren't a flood.
        source.baseline_done = False
        source.etag = source.last_modified = None
        source.next_poll_at = utcnow()

    if session.get(AccountSource, (account_id, source.id)) is None:
        session.add(AccountSource(account_id=account_id, source_id=source.id))
        session.flush()
    return source.id


def _has_subscribers(session, source_id):
    return session.scalar(select(exists().where(AccountSource.source_id == source_id)))


def _stale(source, interval):
    return source.last_polled_at is None or \
        _aware(source.last_polled_at) < utcnow() - timedelta(seconds=2 * interval)


def remove_source(session, account_id, source_id):
    """Removes a source from the account's watchlist. Returns False if it wasn't on it."""
    entry = session.get(AccountSource, (account_id, source_id))
    if entry is None:
        return False
    session.delete(entry)
    session.flush()
    return True


def list_sources(session, account_id):
    week_ago = utcnow() - timedelta(days=7)
    open_count = (select(func.count(Posting.id))
                  .where(Posting.source_id == Source.id, Posting.closed_at.is_(None))
                  .scalar_subquery())
    new_count = (select(func.count(Posting.id))
                 .where(Posting.source_id == Source.id, Posting.is_baseline.is_(False),
                        Posting.first_seen_at >= week_ago)
                 .scalar_subquery())
    rows = session.execute(
        select(Source, open_count, new_count)
        .join(AccountSource, AccountSource.source_id == Source.id)
        .where(AccountSource.account_id == account_id)
        .order_by(AccountSource.created_at)
    ).all()
    return [SourceView(s.id, s.kind, s.slug, s.name, s.last_polled_at, s.last_error,
                       s.baseline_done, open_n or 0, new_n or 0) for s, open_n, new_n in rows]


def recent_postings(session, account_id, limit=50):
    """New (non-baseline) postings from the account's watched sources, one per job."""
    rows = session.scalars(
        select(Posting)
        .join(AccountSource, AccountSource.source_id == Posting.source_id)
        .where(AccountSource.account_id == account_id, Posting.is_baseline.is_(False))
        .order_by(Posting.first_seen_at.desc(), Posting.id.desc())
        .limit(limit * 2)
    ).all()
    unique = {}
    for row in rows:
        unique.setdefault(row.dedupe_key, row)
    return list(unique.values())[:limit]


# ---------- polling ----------

@dataclass(frozen=True)
class ClaimedSource:
    id: int
    kind: str
    slug: str
    etag: str | None
    last_modified: str | None


@dataclass
class PollOutcome:
    source_id: int
    new_posting_ids: list[int] = field(default_factory=list)
    updated: int = 0
    closed: int = 0
    baseline: int = 0
    not_modified: bool = False
    error: str | None = None


def claim_due_sources(db, now=None, limit=10):
    """Leases up to `limit` watched sources whose next poll is due."""
    now = now or utcnow()
    with db.session() as session:
        due = session.scalars(
            select(Source)
            .where(Source.next_poll_at <= now,
                   or_(Source.lease_until.is_(None), Source.lease_until < now),
                   exists().where(AccountSource.source_id == Source.id))
            .order_by(Source.next_poll_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        ).all()
        for source in due:
            source.lease_until = now + LEASE
        return [ClaimedSource(s.id, s.kind, s.slug, s.etag, s.last_modified) for s in due]


def poll_source(db, http, claimed, interval, now=None):
    """Fetches one claimed source and records the result. Never raises for source errors."""
    adapter = sources.ADAPTERS[claimed.kind]
    try:
        result = adapter.fetch(http, claimed.slug, claimed.etag, claimed.last_modified)
    except RateLimited as e:
        return _record_rate_limit(db, claimed.id, str(e), e.retry_after)
    except sources.SourceError as e:
        return _record_failure(db, claimed.id, str(e), interval)
    now = now or utcnow()
    with db.session() as session:
        source = session.get(Source, claimed.id)
        if source is None:
            return PollOutcome(claimed.id, error="source was deleted")
        outcome = apply_fetch(session, source, result, now)
        # Same transaction as the inserts: a saved posting always has its matching task.
        matching.enqueue_new_postings(session, outcome.new_posting_ids)
        source.next_poll_at = now + _jittered(adaptive_interval(interval, source.last_changed_at, now))
        return outcome


def adaptive_interval(interval, last_changed_at, now):
    """The base interval, multiplied for sources that have been quiet for days."""
    if last_changed_at is not None:
        quiet = now - _aware(last_changed_at)
        for threshold, multiplier in QUIET_TIERS:
            if quiet >= threshold:
                return interval * multiplier
    return interval


def _jittered(seconds):
    return timedelta(seconds=seconds * random.uniform(0.9, 1.1))


def _record_rate_limit(db, source_id, message, retry_after):
    """The host rate-limited us: try again once it allows. Not counted as a source failure."""
    with db.session() as session:
        source = session.get(Source, source_id)
        if source is not None:
            source.next_poll_at = utcnow() + timedelta(seconds=retry_after * random.uniform(1.0, 1.1))
            source.last_error = message[:500]
            source.lease_until = None
    log.warning("Source %s is rate-limited: %s", source_id, message)
    return PollOutcome(source_id, error=message)


def _record_failure(db, source_id, message, interval):
    with db.session() as session:
        source = session.get(Source, source_id)
        if source is not None:
            source.consecutive_failures += 1
            backoff = timedelta(seconds=interval) * (2 ** min(source.consecutive_failures - 1, 6))
            source.next_poll_at = utcnow() + min(backoff, MAX_BACKOFF)
            source.last_error = message[:500]
            source.lease_until = None
    log.warning("Polling source %s failed: %s", source_id, message)
    return PollOutcome(source_id, error=message)


def _dedupe_key(posting):
    found = sources.detect(posting.apply_url or posting.canonical_url)
    if found and found[0] in sources.ATS_KINDS and found[2]:
        kind, slug, job_id = found
        return f"{kind}:{slug.lower()}:{job_id.lower()}"
    return normalize_url(posting.apply_url or posting.canonical_url)[:1000]


def _content_hash(p):
    fields = [p.title, p.company, p.location, p.pay, p.description, p.canonical_url, p.apply_url]
    return hashlib.sha256("\x1f".join(fields).encode("utf-8")).hexdigest()


def _aware(dt):
    # SQLite returns naive datetimes; Postgres returns aware ones.
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def apply_fetch(session, source, result, now):
    """
        Desc:
            Records a fetch: inserts new postings, updates changed ones, closes postings that
            left the feed, and finishes the baseline. Returns the new postings to match.
    """
    outcome = PollOutcome(source.id, not_modified=result.not_modified)
    source.last_polled_at = now
    source.lease_until = None
    source.consecutive_failures = 0
    source.last_error = None
    if result.not_modified:
        return outcome
    source.etag, source.last_modified = result.etag, result.last_modified

    baseline = not source.baseline_done
    existing = {p.external_id: p for p in session.scalars(
        select(Posting).where(Posting.source_id == source.id))}
    seen = set()
    new_postings = []

    for item in result.postings:
        if item.external_id in seen:
            continue
        seen.add(item.external_id)
        row = existing.get(item.external_id)
        if not item.active:
            if row is not None and row.closed_at is None:
                row.closed_at = now
                outcome.closed += 1
            continue
        digest = _content_hash(item)
        if row is not None:
            row.last_seen_at = now
            row.closed_at = None
            if row.content_hash != digest:
                _copy_fields(row, item, digest)
                outcome.updated += 1
            continue
        row = Posting(source_id=source.id, external_id=item.external_id,
                      dedupe_key=_dedupe_key(item), first_seen_at=now, last_seen_at=now,
                      is_baseline=baseline, posted_at=item.posted_at)
        _copy_fields(row, item, digest)
        session.add(row)
        new_postings.append(row)

    for external_id, row in existing.items():
        if external_id not in seen and row.closed_at is None:
            row.closed_at = now
            outcome.closed += 1

    session.flush()
    if baseline:
        outcome.baseline = len(new_postings)
    else:
        outcome.new_posting_ids = [row.id for row in new_postings]
    if baseline or new_postings or outcome.updated or outcome.closed:
        source.last_changed_at = now
    source.baseline_done = True
    return outcome


def _copy_fields(row, item, digest):
    row.title = item.title[:500]
    row.company = item.company[:300]
    row.location = item.location[:1000]
    row.pay = item.pay[:300]
    row.description = item.description
    row.canonical_url = item.canonical_url[:2000]
    row.apply_url = item.apply_url[:2000]
    row.extra_json = json.dumps(item.extra) if item.extra else None
    row.content_hash = digest
    if item.posted_at:
        row.posted_at = item.posted_at


def run_due_polls(db, http, interval, limit=10):
    """Claims and polls due sources once. Returns their outcomes."""
    outcomes = []
    for claimed in claim_due_sources(db, limit=limit):
        outcome = poll_source(db, http, claimed, interval)
        log.info("Polled %s:%s: %d new, %d updated, %d closed, %d baseline%s%s",
                 claimed.kind, claimed.slug, len(outcome.new_posting_ids), outcome.updated,
                 outcome.closed, outcome.baseline, " (not modified)" if outcome.not_modified else "",
                 f" error: {outcome.error}" if outcome.error else "")
        outcomes.append(outcome)
    return outcomes
