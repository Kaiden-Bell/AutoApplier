"""Accounts, token auth, encrypted profile storage, and the profile API."""

import io

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend import accounts, admin, crypto
from backend.config import Settings
from backend.main import create_app
from backend.models import Account, Profile
from backend.schemas import ProfileUpdate
from conftest import TEST_DATA_KEY, TEST_TOKEN
from cv_fixtures import projects_and_experience_cv

HEADERS = {"X-AutoApplier-Key": TEST_TOKEN}
KEY = crypto.load_key(TEST_DATA_KEY)


# ---------- crypto ----------

def test_encrypt_round_trip_and_none():
    blob = crypto.encrypt_json(KEY, 1, "master_cv", {"a": [1, "é"]})
    assert crypto.decrypt_json(KEY, 1, "master_cv", blob) == {"a": [1, "é"]}
    assert crypto.encrypt_json(KEY, 1, "master_cv", None) is None
    assert crypto.decrypt_json(KEY, 1, "master_cv", None) is None


def test_ciphertexts_are_randomized():
    assert crypto.encrypt_json(KEY, 1, "f", "x") != crypto.encrypt_json(KEY, 1, "f", "x")


@pytest.mark.parametrize("account_id, field", [(2, "master_cv"), (1, "notify_email")])
def test_ciphertext_is_bound_to_account_and_field(account_id, field):
    blob = crypto.encrypt_json(KEY, 1, "master_cv", "secret")
    with pytest.raises(crypto.DataKeyError, match="could not be decrypted"):
        crypto.decrypt_json(KEY, account_id, field, blob)


def test_wrong_key_and_tampering_are_detected():
    blob = crypto.encrypt_json(KEY, 1, "f", "secret")
    other = crypto.load_key(crypto.generate_key())
    with pytest.raises(crypto.DataKeyError):
        crypto.decrypt_json(other, 1, "f", blob)
    tampered = blob[:-1] + bytes([blob[-1] ^ 1])
    with pytest.raises(crypto.DataKeyError):
        crypto.decrypt_json(KEY, 1, "f", tampered)


@pytest.mark.parametrize("encoded", ["", "not base64!!", crypto.generate_key()[:20]])
def test_bad_keys_are_rejected(encoded):
    with pytest.raises(crypto.DataKeyError):
        crypto.load_key(encoded)


# ---------- tokens ----------

def test_create_account_stores_only_the_token_hash(database):
    with database.session() as session:
        account, token = accounts.create_account(session, label="me")
        assert token.startswith("aa_") and len(token) > 40
        assert account.api_token_hash == accounts.hash_token(token) != token
        found = accounts.find_by_token(session, token)
        assert found.id == account.id and found.label == "me"


def test_unknown_disabled_and_malformed_tokens_are_rejected(database, account_id):
    with database.session() as session:
        assert accounts.find_by_token(session, "aa_nope") is None
        assert accounts.find_by_token(session, "") is None
        assert accounts.find_by_token(session, TEST_TOKEN.removeprefix("aa_")) is None
        session.get(Account, account_id).disabled = True
    with database.session() as session:
        assert accounts.find_by_token(session, TEST_TOKEN) is None


def test_rotate_token_invalidates_the_old_one(database, account_id):
    with database.session() as session:
        new = accounts.rotate_token(session, account_id)
    with database.session() as session:
        assert accounts.find_by_token(session, TEST_TOKEN) is None
        assert accounts.find_by_token(session, new).id == account_id


# ---------- profile storage ----------

def test_profile_is_encrypted_at_rest_and_patch_is_partial(database, account_id):
    cv = projects_and_experience_cv()
    with database.session() as session:
        accounts.update_profile(session, KEY, account_id, ProfileUpdate(
            notify_email="me@example.com", master_cv=cv))
    with database.session() as session:
        row = session.get(Profile, account_id)
        assert b"me@example.com" not in row.notify_email_enc
        assert b"RLPredictor" not in row.master_cv_enc

        out = accounts.update_profile(session, KEY, account_id,
                                      ProfileUpdate(criteria={"target_tokens": ["intern"]}))
    assert out.notify_email == "me@example.com"
    assert out.master_cv == cv
    assert out.criteria.target_tokens == ["intern"]
    assert out.application_profile is None


def test_null_clears_a_field(database, account_id):
    with database.session() as session:
        accounts.update_profile(session, KEY, account_id, ProfileUpdate(notify_email="a@b.co"))
        out = accounts.update_profile(session, KEY, account_id, ProfileUpdate(notify_email=None))
    assert out.notify_email is None


def test_deleting_an_account_deletes_its_profile(database, account_id):
    with database.session() as session:
        accounts.update_profile(session, KEY, account_id, ProfileUpdate(notify_email="a@b.co"))
    with database.session() as session:
        session.delete(session.get(Account, account_id))
    with database.session() as session:
        assert session.scalars(select(Profile)).all() == []


# ---------- API ----------

@pytest.fixture
def client(database, account_id):
    settings = Settings(data_key=TEST_DATA_KEY, worker_count=1)
    with TestClient(create_app(settings, database)) as c:
        yield c


def test_me(client, account_id):
    r = client.get("/api/me", headers=HEADERS)
    assert r.status_code == 200
    assert r.json() == {"id": account_id, "label": "test", "daily_llm_cap": 20,
                        "automation_enabled": False}
    assert client.get("/api/me").status_code == 401


def test_profile_api_round_trip(client):
    assert client.get("/api/profile", headers=HEADERS).json()["master_cv"] is None
    body = {
        "notify_email": "me@example.com",
        "master_cv": projects_and_experience_cv().model_dump(),
        "application_profile": {"first_name": "Test", "needs_sponsorship": False},
    }
    r = client.patch("/api/profile", json=body, headers=HEADERS)
    assert r.status_code == 200, r.text
    got = client.get("/api/profile", headers=HEADERS).json()
    assert got["notify_email"] == "me@example.com"
    assert got["master_cv"]["sections"][2]["heading"] == "Engineering Projects"
    assert got["application_profile"]["first_name"] == "Test"
    assert got["application_profile"]["decline_demographic_questions"] is True
    assert got["updated_at"]


@pytest.mark.parametrize("email", ["not-an-email", "a@b", "x" * 400 + "@example.com"])
def test_profile_rejects_bad_email(client, email):
    assert client.patch("/api/profile", json={"notify_email": email}, headers=HEADERS).status_code == 422


def test_profile_without_data_key_fails_closed(database, account_id):
    with TestClient(create_app(Settings(data_key=""), database)) as c:
        r = c.get("/api/profile", headers=HEADERS)
        assert r.status_code == 503 and "AUTOAPPLIER_DATA_KEY" in r.json()["detail"]


def test_profiles_are_isolated_between_accounts(client, database):
    with database.session() as session:
        _, other_token = accounts.create_account(session, label="other")
    client.patch("/api/profile", json={"notify_email": "me@example.com"}, headers=HEADERS)
    other = client.get("/api/profile", headers={"X-AutoApplier-Key": other_token}).json()
    assert other["notify_email"] is None


# ---------- admin CLI ----------

def test_admin_create_and_list(database):
    out = io.StringIO()
    assert admin.main(["create-account", "--label", "me", "--daily-llm-cap", "5"], database, out) == 0
    token = out.getvalue().strip().splitlines()[-1]
    with database.session() as session:
        assert accounts.find_by_token(session, token).daily_llm_cap == 5
    out = io.StringIO()
    admin.main(["list-accounts"], database, out)
    assert "\tme\tcap=5" in out.getvalue()


def test_admin_gen_key_is_loadable():
    out = io.StringIO()
    admin.main(["gen-key"], out=out)
    assert len(crypto.load_key(out.getvalue().strip())) == 32
