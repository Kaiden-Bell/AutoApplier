"""
    Accounts, API tokens, and the per-account profile synced from the extension.

    v1 accounts are created by the operator (python -m backend.admin create-account), and the
    extension sends the account's token in X-AutoApplier-Key. Google sign-in replaces token
    issuance later (DESIGN.md §8.6) without changing the tables that hang off accounts.id.
"""

import hashlib
import json
import secrets
from dataclasses import dataclass

from sqlalchemy import select

from . import crypto
from .models import Account, Profile, utcnow
from .schemas import ApplicationProfile, Criteria, MasterCV, ProfileOut, ProfileUpdate

TOKEN_PREFIX = "aa_"


@dataclass(frozen=True)
class AccountInfo:
    """The authenticated account, detached from any database session."""
    id: int
    label: str
    daily_llm_cap: int
    automation_enabled: bool


def hash_token(token):
    # Tokens are 256-bit random values, so a plain SHA-256 (not a password hash) is sufficient.
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_token():
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def create_account(session, label="", daily_llm_cap=20):
    """Creates an account and returns (Account, token). The token is not stored; show it once."""
    token = new_token()
    account = Account(label=label, api_token_hash=hash_token(token), daily_llm_cap=daily_llm_cap)
    session.add(account)
    session.flush()
    return account, token


def rotate_token(session, account_id):
    account = session.get(Account, account_id)
    if account is None:
        raise LookupError(f"No account {account_id}.")
    token = new_token()
    account.api_token_hash = hash_token(token)
    return token


def find_by_token(session, token):
    """The active account for a token, or None."""
    if not token or not token.startswith(TOKEN_PREFIX):
        return None
    account = session.scalars(
        select(Account).where(Account.api_token_hash == hash_token(token))
    ).first()
    if account is None or account.disabled:
        return None
    return AccountInfo(id=account.id, label=account.label, daily_llm_cap=account.daily_llm_cap,
                       automation_enabled=account.automation_enabled)


# ---------- profile ----------

_ENCRYPTED_FIELDS = {
    # API field -> (model column, pydantic type for reading back)
    "notify_email": ("notify_email_enc", None),
    "master_cv": ("master_cv_enc", MasterCV),
    "application_profile": ("application_profile_enc", ApplicationProfile),
}


def get_profile(session, key, account_id) -> ProfileOut:
    profile = session.get(Profile, account_id)
    if profile is None:
        return ProfileOut()
    out = {"updated_at": profile.updated_at}
    for name, (column, model) in _ENCRYPTED_FIELDS.items():
        value = crypto.decrypt_json(key, account_id, name, getattr(profile, column))
        out[name] = model.model_validate(value) if (model and value is not None) else value
    if profile.criteria_json:
        out["criteria"] = Criteria.model_validate_json(profile.criteria_json)
    return ProfileOut(**out)


def update_profile(session, key, account_id, update: ProfileUpdate) -> ProfileOut:
    """Applies the fields present in the update; omitted fields are left unchanged."""
    profile = session.get(Profile, account_id)
    if profile is None:
        profile = Profile(account_id=account_id)
        session.add(profile)
    present = update.model_fields_set
    for name, (column, _model) in _ENCRYPTED_FIELDS.items():
        if name in present:
            value = getattr(update, name)
            if hasattr(value, "model_dump"):
                value = value.model_dump(mode="json")
            setattr(profile, column, crypto.encrypt_json(key, account_id, name, value))
    if "criteria" in present:
        profile.criteria_json = (json.dumps(update.criteria.model_dump(mode="json"))
                                 if update.criteria is not None else None)
    profile.updated_at = utcnow()
    session.flush()
    return get_profile(session, key, account_id)
