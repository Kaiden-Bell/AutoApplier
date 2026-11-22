"""
    SQLAlchemy models. Every schema change needs an Alembic migration (src/backend/migrations);
    tests/test_db.py fails if the models and migrations drift apart.

    Phase A tables: accounts and their profiles. Discovery, matches and the task queue arrive with
    later phases (see DESIGN.md §10).
"""

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean, DateTime, ForeignKey, Integer, LargeBinary, String, func,
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
