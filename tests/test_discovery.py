"""Watchlists, polling (baseline, new/updated/closed postings, 304s, backoff, leases), API, worker."""

import threading
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend import accounts, discovery, worker
from backend.config import Settings
from backend.main import create_app
from backend.models import AccountSource, Posting, Source, utcnow
from conftest import TEST_DATA_KEY, TEST_TOKEN
from source_fixtures import (
    GH_BOARD, GH_FEED, SIMPLIFY_FEED, FakeWeb, greenhouse_board, greenhouse_job, simplify_item,
)

INTERVAL = 900
HEADERS = {"X-AutoApplier-Key": TEST_TOKEN}


@pytest.fixture
def web():
    w = FakeWeb()
    w.routes[GH_BOARD] = (200, {"name": "Acme Corp", "content": ""}, None)
    w.routes[GH_FEED] = (200, greenhouse_board(greenhouse_job(1), greenhouse_job(2)), {"etag": '"v1"'})
    return w


def watch(database, web, account_id, url="https://boards.greenhouse.io/acme"):
    with database.session() as session:
        return discovery.add_source(session, web.client(), account_id, url, INTERVAL)


def poll(database, web, now=None):
    """Makes every watched source due, then polls them once."""
    with database.session() as session:
        for source in session.scalars(select(Source)):
            source.next_poll_at = utcnow() - timedelta(seconds=1)
            source.lease_until = None
    return discovery.run_due_polls(database, web.client(), INTERVAL)


def postings(database):
    with database.session() as session:
        return {p.external_id: p for p in session.scalars(select(Posting))}


# ---------- watchlist ----------

def test_add_source_creates_once_and_subscribes(database, web, account_id):
    first = watch(database, web, account_id)
    again = watch(database, web, account_id, "https://job-boards.greenhouse.io/acme/jobs/1")
    assert first == again
    with database.session() as session:
        source = session.get(Source, first)
        assert (source.kind, source.slug, source.name) == ("greenhouse", "acme", "Acme Corp")
        assert session.scalars(select(AccountSource)).all()[0].account_id == account_id


@pytest.mark.parametrize("url, message", [
    ("https://careers.example.com", "isn't a Greenhouse"),
    ("https://boards.greenhouse.io/nope", "No Greenhouse board named “nope”"),
])
def test_add_source_rejects_bad_urls(database, web, account_id, url, message):
    with pytest.raises(discovery.WatchlistError, match=message):
        watch(database, web, account_id, url)


def test_remove_source(database, web, account_id):
    source_id = watch(database, web, account_id)
    with database.session() as session:
        assert discovery.remove_source(session, account_id, source_id)
        assert not discovery.remove_source(session, account_id, source_id)


# ---------- polling ----------

def test_first_poll_is_baseline_then_new_postings_are_reported(database, web, account_id):
    watch(database, web, account_id)
    [outcome] = poll(database, web)
    assert outcome.baseline == 2 and outcome.new_posting_ids == []
    assert all(p.is_baseline for p in postings(database).values())

    web.routes[GH_FEED] = (200, greenhouse_board(greenhouse_job(1), greenhouse_job(2), greenhouse_job(3)),
                           {"etag": '"v2"'})
    [outcome] = poll(database, web)
    rows = postings(database)
    assert outcome.new_posting_ids == [rows["3"].id]
    assert not rows["3"].is_baseline and rows["3"].dedupe_key == "greenhouse:acme:3"
    with database.session() as session:
        assert session.scalars(select(Source)).one().etag == '"v2"'


def test_changed_closed_and_reopened_postings(database, web, account_id):
    watch(database, web, account_id)
    poll(database, web)
    web.routes[GH_FEED] = (200, greenhouse_board(greenhouse_job(1, title="Renamed")), {"etag": '"v2"'})
    [outcome] = poll(database, web)
    rows = postings(database)
    assert (outcome.updated, outcome.closed) == (1, 1)
    assert rows["1"].title == "Renamed" and rows["2"].closed_at is not None

    web.routes[GH_FEED] = (200, greenhouse_board(greenhouse_job(1, title="Renamed"), greenhouse_job(2)), {"etag": '"v3"'})
    [outcome] = poll(database, web)
    assert postings(database)["2"].closed_at is None and outcome.new_posting_ids == []


def test_not_modified_changes_nothing(database, web, account_id):
    watch(database, web, account_id)
    poll(database, web)
    [outcome] = poll(database, web)
    assert outcome.not_modified
    assert web.requests[-1].headers["if-none-match"] == '"v1"'
    assert all(p.closed_at is None for p in postings(database).values())


def test_failures_back_off_and_recover(database, web, account_id):
    watch(database, web, account_id)
    web.routes[GH_FEED] = (503, "down", None)
    [outcome] = poll(database, web)
    assert "HTTP 503" in outcome.error
    with database.session() as session:
        source = session.scalars(select(Source)).one()
        assert source.consecutive_failures == 1 and "503" in source.last_error
        assert discovery._aware(source.next_poll_at) > utcnow() + timedelta(seconds=INTERVAL * 0.9)
        assert source.baseline_done is False

    web.routes[GH_FEED] = (200, greenhouse_board(greenhouse_job(1)), None)
    poll(database, web)
    with database.session() as session:
        source = session.scalars(select(Source)).one()
        assert source.consecutive_failures == 0 and source.last_error is None and source.baseline_done


def test_rate_limit_defers_without_counting_as_failure(database, web, account_id):
    watch(database, web, account_id)
    web.routes[GH_FEED] = (429, "slow down", {"retry-after": "600"})
    [outcome] = poll(database, web)
    assert "429" in outcome.error
    with database.session() as session:
        source = session.scalars(select(Source)).one()
        assert source.consecutive_failures == 0 and "429" in source.last_error
        wait = discovery._aware(source.next_poll_at) - utcnow()
        assert timedelta(seconds=590) < wait < timedelta(seconds=670)


def test_quiet_sources_are_polled_less_often(database, web, account_id):
    now = utcnow()
    assert discovery.adaptive_interval(INTERVAL, None, now) == INTERVAL
    assert discovery.adaptive_interval(INTERVAL, now - timedelta(hours=5), now) == INTERVAL
    assert discovery.adaptive_interval(INTERVAL, now - timedelta(days=3), now) == INTERVAL * 2
    assert discovery.adaptive_interval(INTERVAL, now - timedelta(days=8), now) == INTERVAL * 4

    watch(database, web, account_id)
    poll(database, web)                                   # baseline counts as a change
    with database.session() as session:
        source = session.scalars(select(Source)).one()
        changed = discovery._aware(source.last_changed_at)
        source.last_changed_at = changed - timedelta(days=8)
    poll(database, web)                                   # 304: still quiet
    with database.session() as session:
        source = session.scalars(select(Source)).one()
        wait = discovery._aware(source.next_poll_at) - utcnow()
        assert wait > timedelta(seconds=INTERVAL * 4 * 0.85)
        assert discovery._aware(source.last_changed_at) < utcnow() - timedelta(days=7)


def test_claims_only_due_watched_unleased_sources(database, web, account_id):
    source_id = watch(database, web, account_id)
    assert [c.id for c in discovery.claim_due_sources(database)] == [source_id]
    assert discovery.claim_due_sources(database) == []          # leased
    with database.session() as session:
        session.get(Source, source_id).lease_until = utcnow() - timedelta(seconds=1)
        session.get(Source, source_id).next_poll_at = utcnow() + timedelta(hours=1)
    assert discovery.claim_due_sources(database) == []          # not due
    with database.session() as session:
        session.get(Source, source_id).next_poll_at = utcnow() - timedelta(seconds=1)
        discovery.remove_source(session, account_id, source_id)
    assert discovery.claim_due_sources(database) == []          # nobody watches it


def test_rewatching_a_stale_source_rebaselines(database, web, account_id):
    source_id = watch(database, web, account_id)
    poll(database, web)
    with database.session() as session:
        discovery.remove_source(session, account_id, source_id)
        session.get(Source, source_id).last_polled_at = utcnow() - timedelta(days=3)
    watch(database, web, account_id)
    with database.session() as session:
        source = session.get(Source, source_id)
        assert source.baseline_done is False and source.etag is None
    web.routes[GH_FEED] = (200, greenhouse_board(greenhouse_job(1), greenhouse_job(2), greenhouse_job(9)), None)
    [outcome] = poll(database, web)
    assert outcome.new_posting_ids == [] and postings(database)["9"].is_baseline


def test_simplify_inactive_entries_are_not_stored(database, web, account_id):
    web.routes["https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/.github/scripts/listings.json"] = \
        (200, [simplify_item("a", "https://example.com/a"), simplify_item("b", "https://example.com/b", active=False)], None)
    watch(database, web, account_id, "https://github.com/SimplifyJobs/Summer2027-Internships")
    poll(database, web)
    assert set(postings(database)) == {"a"}


# ---------- de-duplication across sources ----------

def test_recent_postings_shows_one_per_job_for_the_account(database, web, account_id):
    web.routes[SIMPLIFY_FEED] = (200, [], {"etag": '"s1"'})
    watch(database, web, account_id)
    watch(database, web, account_id, "https://github.com/SimplifyJobs/Summer2027-Internships")
    poll(database, web)                                       # both baselines

    web.routes[GH_FEED] = (200, greenhouse_board(greenhouse_job(1), greenhouse_job(2), greenhouse_job(3)), {"etag": '"v2"'})
    web.routes[SIMPLIFY_FEED] = (200, [simplify_item("s3", "https://job-boards.greenhouse.io/acme/jobs/3?utm_source=Simplify")],
                                 {"etag": '"s2"'})
    outcomes = poll(database, web)
    assert sum(len(o.new_posting_ids) for o in outcomes) == 2   # both sources report it...
    with database.session() as session:
        recent = discovery.recent_postings(session, account_id)
        assert len(recent) == 1                               # ...but the account sees it once
        assert recent[0].dedupe_key == "greenhouse:acme:3"


def test_accounts_only_see_their_own_sources(database, web, account_id):
    watch(database, web, account_id)
    with database.session() as session:
        other, _ = accounts.create_account(session, label="other")
        other_id = other.id
    poll(database, web)
    web.routes[GH_FEED] = (200, greenhouse_board(greenhouse_job(1), greenhouse_job(2), greenhouse_job(3)), {"etag": '"v2"'})
    poll(database, web)
    with database.session() as session:
        assert len(discovery.recent_postings(session, account_id)) == 1
        assert discovery.recent_postings(session, other_id) == []
        assert discovery.list_sources(session, other_id) == []


# ---------- API ----------

@pytest.fixture
def client(database, account_id, web):
    with TestClient(create_app(Settings(data_key=TEST_DATA_KEY), database, web.client())) as c:
        yield c


def test_sources_api(client, database, web):
    r = client.post("/api/sources", json={"url": "https://boards.greenhouse.io/acme"}, headers=HEADERS)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["kind"] == "greenhouse" and body["name"] == "Acme Corp" and body["baseline_done"] is False

    poll(database, web)
    [listed] = client.get("/api/sources", headers=HEADERS).json()
    assert listed["open_postings"] == 2 and listed["new_postings_7d"] == 0 and listed["last_polled_at"]

    web.routes[GH_FEED] = (200, greenhouse_board(greenhouse_job(1), greenhouse_job(2), greenhouse_job(3)), {"etag": '"v2"'})
    poll(database, web)
    [posting] = client.get("/api/postings", headers=HEADERS).json()
    assert posting["title"] == "Software Engineer Intern"
    assert posting["apply_url"] == "https://job-boards.greenhouse.io/acme/jobs/3"

    assert client.delete(f"/api/sources/{body['id']}", headers=HEADERS).status_code == 204
    assert client.delete(f"/api/sources/{body['id']}", headers=HEADERS).status_code == 404
    assert client.get("/api/sources", headers=HEADERS).json() == []


def test_sources_api_errors(client, web):
    r = client.post("/api/sources", json={"url": "https://careers.example.com"}, headers=HEADERS)
    assert r.status_code == 422 and "isn't a Greenhouse" in r.json()["detail"]
    web.routes["https://boards-api.greenhouse.io/v1/boards/flaky"] = (500, "boom", None)
    r = client.post("/api/sources", json={"url": "https://boards.greenhouse.io/flaky"}, headers=HEADERS)
    assert r.status_code == 502
    assert client.get("/api/sources").status_code == 401


# ---------- worker ----------

def test_worker_polls_until_stopped(database, web, account_id, monkeypatch):
    watch(database, web, account_id)
    stop = threading.Event()
    calls = []
    original = discovery.run_due_polls

    def run_once(*args, **kwargs):
        calls.append(original(*args, **kwargs))
        stop.set()
        return calls[-1]

    monkeypatch.setattr(discovery, "run_due_polls", run_once)
    worker.run(Settings(worker_tick_seconds=0, poll_interval_seconds=INTERVAL), database, web.client(), stop)
    assert calls and calls[0][0].baseline == 2
