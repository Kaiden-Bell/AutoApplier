"""
    SQLAlchemy models. Every schema change needs an Alembic migration (src/backend/migrations);
    tests/test_db.py fails if the models and migrations drift apart.

    Phase A: accounts and profiles. Phase B: sources (job boards and lists), each account's
    watchlist, and the postings found. Phase C: matches (one account's view of one job) and the
    task queue the worker runs.
"""

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Index, Integer, LargeBinary, String, Text, UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    label: Mapped[str] = mapped_column(String(200), default="")
    # SHA-256 of the account's API token; the token itself is shown once and never stored.
    api_token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    daily_llm_cap: Mapped[int] = mapped_column(Integer, default=20)
    automation_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow,
                                                 server_default=func.now())

    profile: Mapped["Profile"] = relationship(back_populates="account", uselist=False,
                                              cascade="all, delete-orphan")


class Profile(Base):
    """Per-account settings synced from the extension. Personal data is encrypted (crypto.py)."""
    __tablename__ = "profiles"

    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"),
                                            primary_key=True)
    # Encrypted JSON blobs; None until the extension first syncs them.
    notify_email_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    master_cv_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    application_profile_enc: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    # Criteria are matching thresholds, not personal data, and the worker reads them for every
    # posting, so they are stored as plain JSON text.
    criteria_json: Mapped[str | None] = mapped_column(String, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow,
                                                 onupdate=utcnow, server_default=func.now())

    account: Mapped[Account] = relationship(back_populates="profile")


class Source(Base):
    """A job board or list, polled once for every account that watches it."""
    __tablename__ = "sources"
    __table_args__ = (UniqueConstraint("kind", "slug", name="uq_sources_kind_slug"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))          # greenhouse | lever | ashby | simplify
    slug: Mapped[str] = mapped_column(String(200))
    name: Mapped[str] = mapped_column(String(300), default="")
    etag: Mapped[str | None] = mapped_column(String(300), nullable=True)
    last_modified: Mapped[str | None] = mapped_column(String(100), nullable=True)
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # When a poll last found new, changed or closed postings; quiet sources are polled less often.
    last_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_poll_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    # A worker that claimed this source holds it until then (crash-safe claim).
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # False until the first successful poll. Postings seen in that poll are recorded as baseline
    # and never matched, so adding a board doesn't send its whole backlog to the LLM.
    baseline_done: Mapped[bool] = mapped_column(Boolean, default=False)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow,
                                                 server_default=func.now())


class AccountSource(Base):
    """An account's watchlist entry."""
    __tablename__ = "account_sources"

    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"),
                                            primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"),
                                           primary_key=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow,
                                                 server_default=func.now())


class Posting(Base):
    """A job posting as last reported by its source. Shared by all accounts watching the source."""
    __tablename__ = "postings"
    __table_args__ = (
        UniqueConstraint("source_id", "external_id", name="uq_postings_source_external"),
        Index("ix_postings_source_first_seen", "source_id", "first_seen_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id", ondelete="CASCADE"))
    external_id: Mapped[str] = mapped_column(String(200))
    # Identifies the same job across sources ("greenhouse:acme:123" or a normalized URL). Matching
    # de-duplicates per account with it: accounts watch different sources, so a job is only a
    # duplicate for an account that already has it from another source.
    dedupe_key: Mapped[str] = mapped_column(String(1000), index=True)
    company: Mapped[str] = mapped_column(String(300), default="")
    title: Mapped[str] = mapped_column(String(500), default="")
    location: Mapped[str] = mapped_column(String(1000), default="")
    pay: Mapped[str] = mapped_column(String(300), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    canonical_url: Mapped[str] = mapped_column(String(2000), default="")
    apply_url: Mapped[str] = mapped_column(String(2000), default="")
    extra_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), default="")
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_baseline: Mapped[bool] = mapped_column(Boolean, default=False)


class Match(Base):
    """
        One account's record of one job: whether it passed their criteria, the tailored resume,
        and their decision. States and allowed transitions live in matching.py.
    """
    __tablename__ = "matches"
    __table_args__ = (
        # A job reported by two watched sources is matched once per account.
        UniqueConstraint("account_id", "dedupe_key", name="uq_matches_account_dedupe"),
        Index("ix_matches_account_state", "account_id", "state"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    posting_id: Mapped[int] = mapped_column(ForeignKey("postings.id", ondelete="CASCADE"), index=True)
    dedupe_key: Mapped[str] = mapped_column(String(1000))
    state: Mapped[str] = mapped_column(String(30))
    # Criteria rejection reason, failure message, or other status detail for the user.
    reason: Mapped[str] = mapped_column(Text, default="")
    gap_analysis: Mapped[str] = mapped_column(Text, default="")
    selection_notes_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    latex_source: Mapped[str | None] = mapped_column(Text, nullable=True)
    pdf: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    page_count: Mapped[int] = mapped_column(Integer, default=0)
    # Set when tailoring first starts; counts toward the account's daily LLM cap.
    tailor_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True,
                                                                index=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow,
                                                 onupdate=utcnow)


class Task(Base):
    """
        A unit of background work (tasks.py). Claimed with FOR UPDATE SKIP LOCKED and a lease, so
        a crashed worker's task is picked up again once its lease expires.
    """
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(40))
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    # Optional idempotency key: enqueueing a key that is already queued does nothing.
    key: Mapped[str | None] = mapped_column(String(200), nullable=True, unique=True)
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    # Set when the task gave up; dead tasks are kept briefly for inspection, then cleaned up.
    dead: Mapped[bool] = mapped_column(Boolean, default=False)
    last_error: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

