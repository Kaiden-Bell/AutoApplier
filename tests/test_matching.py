"""Task queue, matching new postings per account, tailoring under the daily cap, inbox API, cleanup."""

import base64
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend import accounts, compile_adapter, crypto, discovery, llm_engine, matching, tasks, worker
from backend.config import Settings
from backend.main import create_app
from backend.models import Account, Match, Posting, Source, Task, utcnow
from backend.schemas import BulletPick, EntryPick, ProfileUpdate, SectionPick, TailoredResume
from conftest import TEST_DATA_KEY, TEST_TOKEN
from cv_fixtures import projects_and_experience_cv
from source_fixtures import (
    GH_BOARD, GH_FEED, SIMPLIFY_FEED, FakeWeb, greenhouse_board, greenhouse_job, simplify_item,
)

HEADERS = {"X-AutoApplier-Key": TEST_TOKEN}
INTERVAL = 900
KEY = crypto.load_key(TEST_DATA_KEY)
SETTINGS = Settings(data_key=TEST_DATA_KEY)
INTERN_CRITERIA = {"target_tokens": ["intern"], "banned_tokens": ["senior"], "allowed_locations": [],
                   "minimum_hourly_rate": 0, "minimum_annual_salary": 0}

GOOD = TailoredResume(is_qualified=True, gap_analysis="Strong match.", sections=[
    SectionPick(section_id="s3", entries=[EntryPick(entry_id="s3.e1", bullets=[BulletPick(bullet_id="s3.e1.b1", rewrite=None)])]),
    SectionPick(section_id="s5", entries=[EntryPick(entry_id="s5.e1", bullets=[])]),
])


# ---------- fixtures ----------

@pytest.fixture
def web():
    w = FakeWeb()
    w.routes[GH_BOARD] = (200, {"name": "Acme", "content": ""}, None)
    w.routes[GH_FEED] = (200, greenhouse_board(greenhouse_job(1)), {"etag": '"v1"'})
    return w


@pytest.fixture
def llm(monkeypatch):
    state = {"result": GOOD, "raise": None, "calls": []}

    def generate(job, master_cv, model):
        state["calls"].append(job)
        if state["raise"]:
            raise state["raise"]
        return state["result"]

    monkeypatch.setattr(llm_engine, "generate", generate)
    monkeypatch.setattr(compile_adapter, "compile_latex_source",
                        lambda src, timeout=30: (b"%PDF-1.5 fake", "Output written on resume.pdf (1 page, 9 bytes)."))
    return state


def setup_account(database, account_id, automation=True, criteria=INTERN_CRITERIA, cv=True, cap=20):
    with database.session() as session:
        account = session.get(Account, account_id)
        account.automation_enabled = automation
        account.daily_llm_cap = cap
        accounts.update_profile(session, KEY, account_id, ProfileUpdate(
            master_cv=projects_and_experience_cv() if cv else None, criteria=criteria))


def watch(database, web, account_id, url="https://boards.greenhouse.io/acme"):
    with database.session() as session:
        return discovery.add_source(session, web.client(), account_id, url, INTERVAL)


def poll(database, web):
    with database.session() as session:
        for source in session.scalars(select(Source)):
            source.next_poll_at = utcnow() - timedelta(seconds=1)
    return discovery.run_due_polls(database, web.client(), INTERVAL)


def run_tasks(database, web, rounds=10):
    handlers = worker.handlers(database, web.client(), SETTINGS)
    for _ in range(rounds):
        if not tasks.run(database, handlers, limit=5):
            break


def publish(web, *jobs):
    web.routes[GH_FEED] = (200, greenhouse_board(greenhouse_job(1), *jobs), {"etag": f'"v{len(jobs) + 1}"'})


def matches(database):
    with database.session() as session:
        return session.scalars(select(Match).order_by(Match.id)).all()


@pytest.fixture
def ready_source(database, web, account_id):
    """A watched, baselined board and an account with automation on."""
    setup_account(database, account_id)
    watch(database, web, account_id)
    poll(database, web)
    return account_id


# ---------- task queue ----------

def test_enqueue_is_idempotent_by_key(database):
    with database.session() as session:
        assert tasks.enqueue(session, "k", {"a": 1}, key="x")
        assert not tasks.enqueue(session, "k", {"a": 2}, key="x")
        assert tasks.enqueue(session, "k", {"a": 3})


def test_claim_respects_run_after_and_lease(database):
    with database.session() as session:
        tasks.enqueue(session, "k", key="later", run_after=utcnow() + timedelta(hours=1))
        tasks.enqueue(session, "k", key="now")
    [claimed] = tasks.claim(database, limit=5)
    assert claimed.payload == {} and claimed.attempts == 1
    assert tasks.claim(database, limit=5) == []          # leased / not due


class Flaky:
    def __init__(self, failures):
        self.failures, self.runs, self.gave_up = failures, 0, None

    def run(self, payload):
        self.runs += 1
        if self.runs <= self.failures:
            raise RuntimeError("boom")

    def on_give_up(self, payload, error):
        self.gave_up = error


def _make_due(database):
    with database.session() as session:
        for task in session.scalars(select(Task)):
            task.run_after = utcnow() - timedelta(seconds=1)


def test_failures_back_off_then_succeed(database):
    with database.session() as session:
        tasks.enqueue(session, "k", max_attempts=3)
    handler = Flaky(failures=1)
    tasks.run(database, {"k": handler})
    with database.session() as session:
        task = session.scalars(select(Task)).one()
        assert task.attempts == 1 and "boom" in task.last_error and discovery._aware(task.run_after) > utcnow()
    _make_due(database)
    tasks.run(database, {"k": handler})
    with database.session() as session:
        assert session.scalars(select(Task)).all() == []


def test_gives_up_after_max_attempts(database):
    with database.session() as session:
        tasks.enqueue(session, "k", max_attempts=2)
    handler = Flaky(failures=5)
    for _ in range(3):
        tasks.run(database, {"k": handler})
        _make_due(database)
    assert handler.runs == 2 and "boom" in handler.gave_up
    with database.session() as session:
        assert session.scalars(select(Task)).one().dead


def test_retry_does_not_count_as_an_attempt(database):
    class Later:
        def run(self, payload):
            raise tasks.Retry(60)
    with database.session() as session:
        tasks.enqueue(session, "k", max_attempts=1)
    tasks.run(database, {"k": Later()})
    with database.session() as session:
        task = session.scalars(select(Task)).one()
        assert task.attempts == 0 and not task.dead


# ---------- matching ----------

def test_new_posting_is_matched_and_tailored(database, web, ready_source, llm):
    publish(web, greenhouse_job(2, title="Software Engineer Intern"))
    poll(database, web)
    with database.session() as session:                 # queued in the poll's transaction
        assert session.scalars(select(Task.kind)).all() == ["match_posting"]
    run_tasks(database, web)
    [match] = matches(database)
    assert match.state == "ready" and match.pdf.startswith(b"%PDF")
    assert match.gap_analysis == "Strong match." and match.page_count == 1
    assert "RLPredictor" in match.latex_source and match.tailor_started_at is not None
    assert llm["calls"][0].title == "Software Engineer Intern"


def test_baseline_postings_are_never_matched(database, web, account_id, llm):
    setup_account(database, account_id)
    watch(database, web, account_id)
    poll(database, web)
    run_tasks(database, web)
    assert matches(database) == [] and llm["calls"] == []


def test_criteria_rejection_skips_the_llm(database, web, ready_source, llm):
    publish(web, greenhouse_job(2, title="Senior Software Engineer Intern"))
    poll(database, web)
    run_tasks(database, web)
    [match] = matches(database)
    assert match.state == "criteria_rejected" and "senior" in match.reason
    assert llm["calls"] == []


@pytest.mark.parametrize("setup", [dict(automation=False), dict(cv=False), dict(criteria=None)])
def test_accounts_without_automation_cv_or_criteria_get_no_matches(database, web, account_id, llm, setup):
    setup_account(database, account_id, **setup)
    watch(database, web, account_id)
    poll(database, web)
    publish(web, greenhouse_job(2))
    poll(database, web)
    run_tasks(database, web)
    assert matches(database) == []


def test_not_qualified_and_llm_errors(database, web, ready_source, llm):
    llm["result"] = TailoredResume(is_qualified=False, gap_analysis="Needs Rust.", sections=[])
    publish(web, greenhouse_job(2))
    poll(database, web)
    run_tasks(database, web)
    assert matches(database)[0].state == "llm_rejected"
    assert matches(database)[0].gap_analysis == "Needs Rust."

    llm["raise"] = llm_engine.LLMError("The model refused the request: no")
    publish(web, greenhouse_job(2), greenhouse_job(3))
    poll(database, web)
    run_tasks(database, web)
    failed = matches(database)[1]
    assert failed.state == "failed" and "refused" in failed.reason
    assert len(llm["calls"]) == 2                        # LLM errors are not retried


def test_same_job_from_two_sources_is_matched_once_per_account(database, web, ready_source, llm):
    web.routes[SIMPLIFY_FEED] = (200, [], {"etag": '"s1"'})
    watch(database, web, ready_source, "https://github.com/SimplifyJobs/Summer2027-Internships")
    poll(database, web)
    publish(web, greenhouse_job(2))
    web.routes[SIMPLIFY_FEED] = (200, [simplify_item("x", "https://job-boards.greenhouse.io/acme/jobs/2?utm_source=Simplify")],
                                 {"etag": '"s2"'})
    poll(database, web)
    run_tasks(database, web)
    assert len(matches(database)) == 1 and len(llm["calls"]) == 1


def test_daily_cap_defers_until_tomorrow(database, web, account_id, llm):
    setup_account(database, account_id, cap=1)
    watch(database, web, account_id)
    poll(database, web)
    publish(web, greenhouse_job(2), greenhouse_job(3))
    poll(database, web)
    run_tasks(database, web)
    states = sorted(m.state for m in matches(database))
    assert states == ["deferred", "ready"] and len(llm["calls"]) == 1
    with database.session() as session:
        task = session.scalars(select(Task).where(Task.kind == "tailor_match")).one()
        assert discovery._aware(task.run_after) > utcnow() and task.attempts == 0
        deferred = session.scalars(select(Match).where(Match.state == "deferred")).one()
        assert "Daily limit of 1" in deferred.reason


def test_simplify_description_is_fetched_once_and_shared(database, web, account_id, llm):
    setup_account(database, account_id)
    web.routes[SIMPLIFY_FEED] = (200, [], {"etag": '"s1"'})
    watch(database, web, account_id, "https://github.com/SimplifyJobs/Summer2027-Internships")
    poll(database, web)
    page = ('<script type="application/ld+json">{"@type":"JobPosting","title":"Intern",'
            '"description":"<p>Build compilers.</p>","baseSalary":{"currency":"USD","value":{"value":40,"unitText":"HOUR"}}}</script>')
    web.routes["https://careers.example.com/intern"] = (200, page, None)
    web.routes[SIMPLIFY_FEED] = (200, [simplify_item("x", "https://careers.example.com/intern", title="Compiler Intern")],
                                 {"etag": '"s2"'})
    poll(database, web)
    run_tasks(database, web)
    [match] = matches(database)
    assert match.state == "ready"
    with database.session() as session:
        posting = session.get(Posting, match.posting_id)
        assert posting.description == "Build compilers." and posting.pay == "$40 / hour"
    assert llm["calls"][0].description == "Build compilers."


def test_missing_description_fails_and_lookup_errors_retry(database, web, account_id, llm):
    setup_account(database, account_id)
    web.routes[SIMPLIFY_FEED] = (200, [], {"etag": '"s1"'})
    watch(database, web, account_id, "https://github.com/SimplifyJobs/Summer2027-Internships")
    poll(database, web)
    web.routes["https://careers.example.com/a"] = (200, "<html>nothing</html>", None)
    web.routes["https://careers.example.com/b"] = (500, "down", None)
    web.routes[SIMPLIFY_FEED] = (200, [simplify_item("a", "https://careers.example.com/a", title="Intern A"),
                                       simplify_item("b", "https://careers.example.com/b", title="Intern B")],
                                 {"etag": '"s2"'})
    poll(database, web)
    run_tasks(database, web)
    by_state = {m.state: m for m in matches(database)}
    assert "No job description" in by_state["failed"].reason
    assert "queued" in by_state                          # lookup error: retried later
    assert llm["calls"] == []


def test_crashed_tailoring_resumes(database, web, ready_source, llm):
    publish(web, greenhouse_job(2))
    poll(database, web)
    run_tasks(database, web)
    with database.session() as session:
        match = session.scalars(select(Match)).one()
        match.state = "tailoring"
        match.pdf = None
        tasks.enqueue(session, "tailor_match", {"match_id": match.id})
    run_tasks(database, web)
    assert matches(database)[0].state == "ready"


# ---------- state machine ----------

def test_transitions():
    match = Match(state="ready")
    matching.transition(match, "approved")
    with pytest.raises(matching.InvalidTransition):
        matching.transition(match, "ready")
    assert all(target in matching.STATES for targets in matching.TRANSITIONS.values() for target in targets)


# ---------- API ----------

@pytest.fixture
def client(database, account_id, web):
    with TestClient(create_app(SETTINGS, database, web.client())) as c:
        yield c


def test_inbox_api(client, database, web, ready_source, llm):
    publish(web, greenhouse_job(2), greenhouse_job(3, title="Senior Intern"))
    poll(database, web)
    run_tasks(database, web)
    inbox = client.get("/api/matches", headers=HEADERS).json()
    assert [m["state"] for m in inbox] == ["ready"]        # criteria rejections are hidden
    ready = inbox[0]
    assert ready["has_resume"] and ready["title"] == "Software Engineer Intern"
    assert ready["apply_url"] == "https://job-boards.greenhouse.io/acme/jobs/2"
    rejected = client.get("/api/matches?state=criteria_rejected", headers=HEADERS).json()
    assert len(rejected) == 1
    assert client.get("/api/matches?state=bogus", headers=HEADERS).status_code == 422

    resume = client.get(f"/api/matches/{ready['id']}/resume", headers=HEADERS).json()
    assert base64.b64decode(resume["resume_pdf_base64"]).startswith(b"%PDF")

    r = client.post(f"/api/matches/{ready['id']}/decision", json={"decision": "approve"}, headers=HEADERS)
    assert r.status_code == 200 and r.json()["state"] == "approved"
    r = client.post(f"/api/matches/{ready['id']}/decision", json={"decision": "approve"}, headers=HEADERS)
    assert r.status_code == 409


def test_retry_api(client, database, web, ready_source, llm):
    llm["raise"] = llm_engine.LLMError("upstream down")
    publish(web, greenhouse_job(2))
    poll(database, web)
    run_tasks(database, web)
    [failed] = client.get("/api/matches", headers=HEADERS).json()
    assert failed["state"] == "failed"
    llm["raise"] = None
    r = client.post(f"/api/matches/{failed['id']}/retry", headers=HEADERS)
    assert r.status_code == 200 and r.json()["state"] == "queued"
    run_tasks(database, web)
    assert client.get("/api/matches", headers=HEADERS).json()[0]["state"] == "ready"


def test_matches_are_private(client, database, web, ready_source, llm):
    publish(web, greenhouse_job(2))
    poll(database, web)
    run_tasks(database, web)
    [mine] = client.get("/api/matches", headers=HEADERS).json()
    with database.session() as session:
        _, token = accounts.create_account(session, label="other")
    other = {"X-AutoApplier-Key": token}
    assert client.get("/api/matches", headers=other).json() == []
    assert client.get(f"/api/matches/{mine['id']}/resume", headers=other).status_code == 404
    assert client.post(f"/api/matches/{mine['id']}/decision", json={"decision": "skip"}, headers=other).status_code == 404


def test_account_automation_toggle(client):
    r = client.patch("/api/account", json={"automation_enabled": True}, headers=HEADERS)
    assert r.status_code == 200 and r.json()["automation_enabled"] is True
    assert client.get("/api/me", headers=HEADERS).json()["automation_enabled"] is True


# ---------- cleanup ----------

def test_cleanup_removes_old_rejections_and_closed_postings(database, web, ready_source, llm):
    publish(web, greenhouse_job(2), greenhouse_job(3, title="Senior Intern"))
    poll(database, web)
    run_tasks(database, web)
    publish(web)                                      # jobs 2 and 3 close
    poll(database, web)
    old = utcnow() - timedelta(days=31)
    with database.session() as session:
        for match in session.scalars(select(Match)):
            match.updated_at = old
        for posting in session.scalars(select(Posting).where(Posting.closed_at.is_not(None))):
            posting.closed_at = old
    removed = matching.cleanup(database)
    assert removed["matches"] == 1                    # the criteria rejection
    assert removed["postings"] == 1                   # job 3; job 2 still has its ready match
    assert [m.state for m in matches(database)] == ["ready"]


def test_admin_set_cap(database, account_id):
    import io
    from backend import admin
    out = io.StringIO()
    assert admin.main(["set-cap", str(account_id), "3"], database, out) == 0
    with database.session() as session:
        assert session.get(Account, account_id).daily_llm_cap == 3
