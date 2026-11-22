"""
    Turning new postings into per-account matches, and tailoring them.

    Flow (all in the worker, via tasks.py):
      poll finds a new posting ─► match_posting task
        for each account watching the source with automation on, a CV and criteria:
          already has this job (same dedupe_key from another source) ─► skip
          criteria fail ─► match "criteria_rejected" (no LLM call)
          criteria pass ─► match "queued" + tailor_match task
      tailor_match task
        over today's LLM cap ─► "deferred" until 00:00 UTC
        no description (Simplify) ─► fetch it once onto the shared posting (sources/enrich.py)
        pipeline.tailor ─► "ready" (PDF stored) | "llm_rejected" | "failed"

    Match states and the transitions allowed between them are below. Phase D (email approval)
    and Phase E (assisted apply) use the later states.
"""

import base64
import json
import logging
from datetime import datetime, time, timedelta, timezone

from sqlalchemy import delete, func, select

from . import accounts, crypto, llm_engine, pipeline, sources, tasks
from .criteria_matcher import eval_job_criteria
from .models import Account, AccountSource, Match, Posting, Profile, Task, utcnow
from .schemas import Job
from .sources import enrich

log = logging.getLogger("autoapplier.matching")

# ---------- state machine ----------

TRANSITIONS = {
    "criteria_rejected": set(),
    "queued": {"tailoring", "deferred", "failed", "skipped"},
    "deferred": {"tailoring", "deferred", "failed", "skipped"},
    "tailoring": {"ready", "llm_rejected", "failed", "queued"},
    "ready": {"emailed", "approved", "skipped", "expired"},
    "emailed": {"approved", "skipped", "expired"},
    "llm_rejected": {"skipped"},
    "failed": {"queued", "skipped"},
    "approved": {"applying", "skipped"},
    "skipped": set(),
    "expired": {"approved", "skipped"},
    "applying": {"filled", "apply_failed"},
    "filled": {"submitted", "apply_failed"},
    "apply_failed": {"applying", "skipped"},
    "submitted": set(),
}
STATES = frozenset(TRANSITIONS)
# Shown in the inbox by default: everything except silent criteria rejections.
INBOX_STATES = STATES - {"criteria_rejected"}


class InvalidTransition(ValueError):
    pass


def transition(match, new_state, reason=None):
    if new_state not in TRANSITIONS.get(match.state, set()):
        raise InvalidTransition(f"A match can't go from {match.state} to {new_state}.")
    match.state = new_state
    if reason is not None:
        match.reason = reason
    match.updated_at = utcnow()


# ---------- matching ----------

def enqueue_new_postings(session, posting_ids):
    for posting_id in posting_ids:
        tasks.enqueue(session, "match_posting", {"posting_id": posting_id},
                      key=f"match_posting:{posting_id}")


def _job(posting):
    return Job(title=posting.title or "", location=posting.location or "", pay=posting.pay or "",
               description=posting.description or posting.title or "(no description)",
               url=posting.apply_url or None)


class MatchPosting:
    """Task handler: decides which accounts get a match for a new posting."""

    def __init__(self, db):
        self.db = db

    def run(self, payload):
        with self.db.session() as session:
            posting = session.get(Posting, payload["posting_id"])
            if posting is None or posting.closed_at is not None:
                return
            rows = session.execute(
                select(Account.id, Profile.criteria_json)
                .join(AccountSource, AccountSource.account_id == Account.id)
                .join(Profile, Profile.account_id == Account.id)
                .where(AccountSource.source_id == posting.source_id,
                       Account.automation_enabled.is_(True), Account.disabled.is_(False),
                       Profile.master_cv_enc.is_not(None), Profile.criteria_json.is_not(None))
            ).all()
            job = {"title": posting.title, "location": posting.location, "pay": posting.pay}
            for account_id, criteria_json in rows:
                exists = session.scalar(select(Match.id).where(
                    Match.account_id == account_id, Match.dedupe_key == posting.dedupe_key))
                if exists is not None:
                    continue        # this account already has the job from another source
                passed, reason = eval_job_criteria(job, json.loads(criteria_json))
                match = Match(account_id=account_id, posting_id=posting.id,
                              dedupe_key=posting.dedupe_key,
                              state="queued" if passed else "criteria_rejected",
                              reason="" if passed else reason)
                session.add(match)
                session.flush()
                if passed:
                    tasks.enqueue(session, "tailor_match", {"match_id": match.id},
                                  key=f"tailor_match:{match.id}", max_attempts=4)


def _start_of_next_utc_day(now):
    tomorrow = (now.astimezone(timezone.utc) + timedelta(days=1)).date()
    return datetime.combine(tomorrow, time(0, 5), tzinfo=timezone.utc)


def tailored_today(session, account_id, now=None):
    now = now or utcnow()
    day_start = datetime.combine(now.astimezone(timezone.utc).date(), time(0), tzinfo=timezone.utc)
    return session.scalar(select(func.count(Match.id)).where(
        Match.account_id == account_id, Match.tailor_started_at >= day_start)) or 0


class TailorMatch:
    """Task handler: tailors one queued match within the account's daily LLM cap."""

    def __init__(self, db, http, settings):
        self.db = db
        self.http = http
        self.settings = settings

    def run(self, payload):
        match_id = payload["match_id"]
        retry_in = None
        with self.db.session() as session:
            match = session.get(Match, match_id)
            # "tailoring" too: a worker that crashed mid-run leaves the match there.
            if match is None or match.state not in ("queued", "deferred", "tailoring"):
                return
            posting = session.get(Posting, match.posting_id)
            # Lock the account row so concurrent workers can't both pass the cap check.
            account = session.scalars(select(Account).where(Account.id == match.account_id)
                                      .with_for_update()).one()
            now = utcnow()
            already_counted = match.tailor_started_at is not None
            if not already_counted and tailored_today(session, account.id, now) >= account.daily_llm_cap:
                if match.state != "deferred":
                    transition(match, "deferred",
                               f"Daily limit of {account.daily_llm_cap} tailored resumes reached; "
                               "continues after 00:00 UTC.")
                retry_in = int((_start_of_next_utc_day(now) - now).total_seconds())
            needs_description = not posting.description
            apply_url = posting.apply_url
        if retry_in is not None:     # raised after the commit, so the "deferred" state is kept
            raise tasks.Retry(retry_in, "daily cap reached")

        if needs_description:
            self._fill_description(match_id, posting.id, apply_url)

        with self.db.session() as session:
            match = session.get(Match, match_id)
            posting = session.get(Posting, match.posting_id)
            if not posting.description:
                if "failed" in TRANSITIONS[match.state]:
                    transition(match, "failed", "No job description could be found for this posting.")
                return
            if match.state != "tailoring":
                transition(match, "tailoring")
            if match.tailor_started_at is None:
                match.tailor_started_at = utcnow()
            job = _job(posting)
            master_cv = self._master_cv(session, match.account_id)

        try:
            result = pipeline.tailor(job, master_cv, self.settings)
        except llm_engine.LLMError as e:
            # Not retried: the model call may already have cost money, and a refusal won't change.
            self._finish(match_id, "failed", f"Tailoring failed: {e}")
            return
        self._store(match_id, result)

    def _fill_description(self, match_id, posting_id, url):
        try:
            details = enrich.fetch_details(self.http, url)
        except sources.SourceError as e:
            raise tasks.Retry(15 * 60, f"description lookup failed: {e}") from e
        if not details:
            return
        with self.db.session() as session:
            posting = session.get(Posting, posting_id)
            # Shared by every account matching this posting, so it is fetched only once.
            posting.description = details["description"]
            if details.get("pay") and not posting.pay:
                posting.pay = details["pay"][:300]
            if details.get("location") and not posting.location:
                posting.location = details["location"][:1000]

    def _master_cv(self, session, account_id):
        key = crypto.load_key(self.settings.data_key)
        profile = accounts.get_profile(session, key, account_id)
        if profile.master_cv is None:
            raise RuntimeError("The account has no master CV.")
        return profile.master_cv

    def _store(self, match_id, result):
        with self.db.session() as session:
            match = session.get(Match, match_id)
            match.gap_analysis = result.gap_analysis or ""
            match.selection_notes_json = json.dumps(result.selection_notes) if result.selection_notes else None
            match.latex_source = result.latex_source or None
            match.page_count = result.page_count
            if result.stage == "llm":
                transition(match, "llm_rejected", result.reason)
            elif result.stage == "complete":
                match.pdf = base64.b64decode(result.resume_pdf_base64)
                transition(match, "ready", result.reason)
            else:
                log_tail = (result.compile_log or "")[-2000:]
                transition(match, "failed", f"{result.reason}\n\n{log_tail}".strip())

    def _finish(self, match_id, state, reason):
        with self.db.session() as session:
            transition(session.get(Match, match_id), state, reason)

    def on_give_up(self, payload, error):
        with self.db.session() as session:
            match = session.get(Match, payload["match_id"])
            if match is not None and "failed" in TRANSITIONS.get(match.state, ()):
                transition(match, "failed", f"Gave up after repeated errors: {error}")


# ---------- user actions ----------

def list_matches(session, account_id, states=None, limit=50):
    states = states or INBOX_STATES
    return session.execute(
        select(Match, Posting)
        .join(Posting, Posting.id == Match.posting_id)
        .where(Match.account_id == account_id, Match.state.in_(states))
        .order_by(Match.created_at.desc(), Match.id.desc())
        .limit(limit)
    ).all()


def get_match(session, account_id, match_id):
    match = session.get(Match, match_id)
    if match is None or match.account_id != account_id:
        return None
    return match


def decide(session, account_id, match_id, decision):
    """Records the user's approve/skip decision. Raises LookupError or InvalidTransition."""
    match = get_match(session, account_id, match_id)
    if match is None:
        raise LookupError(match_id)
    transition(match, "approved" if decision == "approve" else "skipped")
    match.decided_at = utcnow()
    return match


def retry(session, account_id, match_id):
    """Queues a failed match for tailoring again (still subject to the daily cap)."""
    match = get_match(session, account_id, match_id)
    if match is None:
        raise LookupError(match_id)
    transition(match, "queued", "")
    key = f"tailor_match:{match.id}"
    session.execute(delete(Task).where(Task.key == key, Task.dead.is_(True)))
    tasks.enqueue(session, "tailor_match", {"match_id": match.id}, key=key, max_attempts=4)
    return match


# ---------- retention ----------

REJECTED_RETENTION = timedelta(days=30)
CLOSED_POSTING_RETENTION = timedelta(days=30)
DEAD_TASK_RETENTION = timedelta(days=7)


def cleanup(db, now=None):
    """
        Desc:
            Deletes rows nobody needs any more, so a small managed database (Supabase's free
            tier is 500 MB) doesn't fill up: rejected matches after 30 days, dead tasks after 7,
            and closed postings after 30 days unless a remaining match refers to them.
        Returns:
            dict: rows deleted per table.
    """
    now = now or utcnow()
    with db.session() as session:
        rejected = session.execute(delete(Match).where(
            Match.state.in_(("criteria_rejected", "llm_rejected", "skipped", "expired")),
            Match.updated_at < now - REJECTED_RETENTION)).rowcount
        dead = session.execute(delete(Task).where(
            Task.dead.is_(True), Task.created_at < now - DEAD_TASK_RETENTION)).rowcount
        referenced = select(Match.posting_id)
        closed = session.execute(delete(Posting).where(
            Posting.closed_at.is_not(None), Posting.closed_at < now - CLOSED_POSTING_RETENTION,
            Posting.id.not_in(referenced))).rowcount
    return {"matches": rejected, "tasks": dead, "postings": closed}


