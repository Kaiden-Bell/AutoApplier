"""Source adapters, URL detection, text helpers, JSON-LD port, enrichment and the HTTP client."""

from datetime import datetime, timezone

import pytest

from backend.criteria_matcher import parse_pay
from backend.sources import ashby, detect, enrich, greenhouse, jsonld, lever, simplify
from backend.sources.base import (
    SourceError, SourceNotFound, format_pay, html_to_text, normalize_url, parse_datetime,
)
from backend.sources.http import RateLimited, parse_retry_after
from source_fixtures import (
    ASHBY_FEED, GH_BOARD, GH_FEED, LEVER_FEED, SIMPLIFY_FEED, FakeWeb, ashby_board, ashby_job,
    greenhouse_board, greenhouse_job, lever_posting, simplify_item,
)

LEVER_ID = "6ed76ce8-4156-4b60-b120-403538bd66cd"
ASHBY_ID = "34413f8d-26bf-4bbc-8ade-eb309a0e2245"


@pytest.fixture
def web():
    return FakeWeb()


# ---------- adapters ----------

def test_greenhouse_feed(web):
    web.routes[GH_FEED] = (200, greenhouse_board(greenhouse_job(11), greenhouse_job(12, pay_cents=None)),
                           {"etag": '"v1"'})
    result = greenhouse.fetch(web.client(), "acme")
    assert result.etag == '"v1"' and len(result.postings) == 2
    p = result.postings[0]
    assert (p.external_id, p.title, p.company, p.location) == ("11", "Software Engineer Intern", "Acme", "Reno, NV")
    assert p.pay == "$40 - $50 / hour" and parse_pay(p.pay) == (50.0, "hourly")
    assert p.description == "Build APIs & tools.\n\n- Python\n- SQL"
    assert p.apply_url == "https://job-boards.greenhouse.io/acme/jobs/11"
    assert p.canonical_url.startswith("https://careers.acme.example/")
    assert p.posted_at.isoformat() == "2026-11-10T04:49:33-05:00"
    assert result.postings[1].pay == ""


def test_greenhouse_annual_pay_and_board_name(web):
    job = greenhouse_job(1, pay_cents=(85_000_00, 105_000_00), pay_title="US Annual Base Pay")
    web.routes[GH_FEED] = (200, greenhouse_board(job), None)
    web.routes[GH_BOARD] = (200, {"name": "Acme Corp", "content": ""}, None)
    assert greenhouse.fetch(web.client(), "acme").postings[0].pay == "$85,000 - $105,000 / year"
    assert greenhouse.check(web.client(), "acme") == "Acme Corp"


def test_lever_feed(web):
    salary = {"currency": "USD", "interval": "per-hour-wage", "min": 30, "max": 45}
    web.routes[LEVER_FEED] = (200, [lever_posting(LEVER_ID, salary=salary)], None)
    p = lever.fetch(web.client(), "acme").postings[0]
    assert p.title == "Backend Engineer" and p.company == "acme"
    assert p.location == "Remote; Seattle, WA; New York, NY"
    assert p.pay == "$30 - $45 / hour"
    assert "You will build services." in p.description
    assert "What you'll do\n- Ship features\n- Write tests" in p.description
    assert p.description.endswith("We sponsor visas.")
    assert p.apply_url.endswith("/apply") and p.posted_at.year == 2026


def test_ashby_feed_skips_unlisted(web):
    web.routes[ASHBY_FEED] = (200, ashby_board(ashby_job(ASHBY_ID), ashby_job("x" * 36, listed=False)), None)
    postings = ashby.fetch(web.client(), "acme").postings
    p = postings[0]
    assert p.title == "Software Engineer, Security"
    assert p.location == "Remote; New York, NY (HQ); Miami, FL"
    assert p.pay == "$211.4K - $290.6K" and parse_pay(p.pay) == (290600.0, "annual")
    assert p.description.startswith("ABOUT ACME")
    assert postings[1].active is False


def test_simplify_feed(web):
    items = [simplify_item("a1", "https://job-boards.greenhouse.io/acme/jobs/11?utm_source=Simplify&ref=Simplify"),
             simplify_item("a2", "https://example.com/job", active=False)]
    web.routes[SIMPLIFY_FEED] = (200, items, {"etag": '"s1"'})
    result = simplify.fetch(web.client(), "Summer2027-Internships")
    first, second = result.postings
    assert first.company == "Acme" and first.location == "San Jose, CA" and first.description == ""
    assert first.extra["terms"] == ["Summer 2027"] and first.active and not second.active


def test_not_modified(web):
    web.routes[GH_FEED] = (200, greenhouse_board(greenhouse_job(1)), {"etag": '"v1"'})
    result = greenhouse.fetch(web.client(), "acme", etag='"v1"')
    assert result.not_modified and result.postings == [] and result.etag == '"v1"'
    assert web.requests[-1].headers["if-none-match"] == '"v1"'


def test_missing_board_raises_not_found(web):
    with pytest.raises(SourceNotFound):
        greenhouse.check(web.client(), "nope")
    with pytest.raises(SourceNotFound):
        lever.fetch(web.client(), "nope")


# ---------- detection and URLs ----------

@pytest.mark.parametrize("url, expected", [
    ("https://job-boards.greenhouse.io/acme/jobs/123", ("greenhouse", "acme", "123")),
    ("https://boards.greenhouse.io/acme", ("greenhouse", "acme", None)),
    ("boards.greenhouse.io/embed/job_board?for=acme&token=55", ("greenhouse", "acme", "55")),
    ("https://boards-api.greenhouse.io/v1/boards/acme/jobs/7", ("greenhouse", "acme", "7")),
    (f"https://jobs.lever.co/acme/{LEVER_ID}/apply", ("lever", "acme", LEVER_ID)),
    ("https://jobs.lever.co/acme", ("lever", "acme", None)),
    (f"https://jobs.ashbyhq.com/acme/{ASHBY_ID}/application", ("ashby", "acme", ASHBY_ID)),
    ("https://github.com/SimplifyJobs/Summer2027-Internships", ("simplify", "Summer2027-Internships", None)),
    ("https://careers.acme.example/jobs/1", None),
    ("https://jobs.lever.co/", None),
    ("https://evil.example/boards.greenhouse.io/acme", None),
    ("", None),
])
def test_detect(url, expected):
    assert detect(url) == expected


def test_normalize_url_strips_tracking():
    a = normalize_url("https://WWW.Example.com/jobs/1/?utm_source=Simplify&ref=Simplify&id=9#apply")
    assert a == "https://example.com/jobs/1?id=9"


def test_format_pay_and_dates():
    assert format_pay(20, 20, "USD", "hour") == "$20 / hour"
    assert format_pay(None, None, "USD", "year") == ""
    assert format_pay(50000, None, "EUR", "year") == "EUR 50,000 / year"
    assert parse_datetime(1786469891368).year == 2026
    assert parse_datetime("garbage") is None


@pytest.mark.parametrize("raw, expected", [
    ("R&D role, 5+ years", "R&D role, 5+ years"),
    ("&lt;p&gt;Hi&lt;/p&gt;&lt;p&gt;There&lt;/p&gt;", "Hi\nThere"),
    ("<style>x{}</style><ul><li>A</li><li>B</li></ul>", "- A\n- B"),
])
def test_html_to_text(raw, expected):
    assert html_to_text(raw) == expected


# ---------- JSON-LD port (mirrors tests/jsonld.test.js) ----------

def test_jsonld_matches_extension_parser():
    posting = {
        "@context": "https://schema.org", "@type": "JobPosting", "title": "Backend Software Engineer",
        "description": "&lt;p&gt;Build &lt;strong&gt;APIs&lt;/strong&gt;.&lt;/p&gt;&lt;ul&gt;&lt;li&gt;Python&lt;/li&gt;&lt;li&gt;SQL&lt;/li&gt;&lt;/ul&gt;",
        "hiringOrganization": {"@type": "Organization", "name": "Acme"},
        "jobLocation": {"@type": "Place", "address": {"addressLocality": "Reno", "addressRegion": "NV", "addressCountry": "US"}},
        "baseSalary": {"@type": "MonetaryAmount", "currency": "USD", "value": {"@type": "QuantitativeValue", "minValue": 85000, "maxValue": 105000, "unitText": "YEAR"}},
    }
    job = jsonld.parse_job_posting(posting)
    assert job["title"] == "Backend Software Engineer"
    assert job["location"] == "Reno, NV, US"
    assert job["pay"] == "$85,000 - $105,000 / year"
    assert job["description"].startswith("Company: Acme")
    assert "- Python\n- SQL" in job["description"]


def test_jsonld_graph_remote_and_hourly():
    data = {"@graph": [{"@type": "WebPage"}, {"@type": ["Thing", "JobPosting"], "title": "Firmware", "description": "x",
                                               "jobLocationType": "TELECOMMUTE",
                                               "baseSalary": {"currency": "USD", "value": {"value": 32.5, "unitText": "HOUR"}}}]}
    job = jsonld.parse_job_posting(jsonld.find_job_posting(data))
    assert job["location"] == "Remote" and job["pay"] == "$32.5 / hour"


def test_extract_from_html_skips_bad_blocks():
    page = ('<html><script type="application/ld+json">{not json</script>'
            '<script type="application/ld+json">{"@type":"Organization"}</script>'
            '<script type="application/ld+json">{"@type":"JobPosting","title":"Intern","description":"<p>Do &amp; learn</p>"}</script></html>')
    job = jsonld.extract_from_html(page)
    assert job["title"] == "Intern" and job["description"] == "Do & learn"
    assert jsonld.extract_from_html("<html></html>") is None


# ---------- enrichment ----------

def test_enrich_uses_ats_api_for_ats_urls(web):
    web.routes["https://boards-api.greenhouse.io/v1/boards/acme/jobs/11?pay_transparency=true"] = (200, greenhouse_job(11), None)
    details = enrich.fetch_details(web.client(), "https://job-boards.greenhouse.io/acme/jobs/11?utm_source=Simplify")
    assert details["description"].startswith("Build APIs") and details["pay"] == "$40 - $50 / hour"


def test_enrich_ashby_reads_the_board(web):
    web.routes[ASHBY_FEED] = (200, ashby_board(ashby_job(ASHBY_ID)), None)
    details = enrich.fetch_details(web.client(), f"https://jobs.ashbyhq.com/acme/{ASHBY_ID}")
    assert details["description"].startswith("ABOUT ACME")


def test_enrich_falls_back_to_page_json_ld(web):
    page = '<script type="application/ld+json">{"@type":"JobPosting","title":"Intern","description":"Learn things."}</script>'
    web.routes["https://careers.example.com/job/1"] = (200, page, {"content-type": "text/html"})
    assert enrich.fetch_details(web.client(), "https://careers.example.com/job/1")["description"] == "Learn things."
    web.routes["https://careers.example.com/job/2"] = (200, "<html>No data</html>", None)
    assert enrich.fetch_details(web.client(), "https://careers.example.com/job/2") is None


# ---------- HTTP client ----------

def test_client_sends_generic_user_agent(web):
    web.routes["https://a.example/x"] = (200, {}, None)
    web.client().get("https://a.example/x")
    assert web.requests[0].headers["user-agent"].startswith("AutoApplier/")


def test_client_errors_and_size_cap(web, monkeypatch):
    web.routes["https://a.example/500"] = (500, "boom", None)
    with pytest.raises(SourceError, match="HTTP 500"):
        web.client().get("https://a.example/500")
    from backend.sources import http as http_module
    monkeypatch.setattr(http_module, "MAX_BYTES", 10)
    web.routes["https://a.example/big"] = (200, "x" * 100, None)
    with pytest.raises(SourceError, match="larger than"):
        web.client().get("https://a.example/big")


def test_client_spaces_requests_to_the_same_host(web):
    web.routes["https://a.example/1"] = (200, {}, None)
    web.routes["https://b.example/1"] = (200, {}, None)
    sleeps, now = [], [100.0]
    client = web.client(min_host_interval=1.0, sleep=sleeps.append, clock=lambda: now[0])
    client.get("https://a.example/1")
    client.get("https://b.example/1")      # different host: no wait
    client.get("https://a.example/1")      # same host, no time passed: wait 1 s
    assert sleeps == [1.0]


def test_client_budget_queues_requests_per_host(web):
    web.routes["https://a.example/1"] = (200, {}, None)
    sleeps, now = [], [100.0]
    client = web.client(min_host_interval=1.0, requests_per_minute=30, sleep=sleeps.append,
                        clock=lambda: now[0])
    for _ in range(3):
        client.get("https://a.example/1")  # 30/min = one slot every 2 s, reserved in order
    assert sleeps == [2.0, 4.0]


def test_429_cools_down_the_whole_host(web):
    web.routes["https://a.example/board1"] = (429, "slow down", {"retry-after": "120"})
    web.routes["https://a.example/board2"] = (200, {}, None)
    web.routes["https://b.example/x"] = (200, {}, None)
    now = [100.0]
    client = web.client(clock=lambda: now[0])
    with pytest.raises(RateLimited) as info:
        client.get("https://a.example/board1")
    assert info.value.retry_after == 120
    requests_before = len(web.requests)
    with pytest.raises(RateLimited) as info:
        client.get("https://a.example/board2")       # same host: fails fast, no request sent
    assert len(web.requests) == requests_before and 100 <= info.value.retry_after <= 121
    client.get("https://b.example/x")                # other hosts are unaffected
    now[0] += 121
    assert client.cooldown_remaining("https://a.example/board2") == 0
    client.get("https://a.example/board2")


def test_503_with_retry_after_is_a_rate_limit_but_plain_503_is_not(web):
    web.routes["https://a.example/1"] = (503, "busy", {"retry-after": "30"})
    web.routes["https://b.example/1"] = (503, "down", None)
    with pytest.raises(RateLimited):
        web.client().get("https://a.example/1")
    with pytest.raises(SourceError) as info:
        web.client().get("https://b.example/1")
    assert not isinstance(info.value, RateLimited)


@pytest.mark.parametrize("header, expected", [
    ("90", 90), (None, 900), ("garbage", 900), ("999999", 6 * 3600),
    ("Sun, 22 Nov 2026 01:10:00 GMT", 600),
])
def test_parse_retry_after(header, expected):
    now = datetime(2026, 11, 22, 1, 0, 0, tzinfo=timezone.utc)
    assert parse_retry_after(header, now=now) == expected
