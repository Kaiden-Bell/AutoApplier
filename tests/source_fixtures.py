"""
    Job-source responses for tests. Field names and nesting copy real responses recorded on
    2026-11-22 (Greenhouse, Lever, Ashby boards and SimplifyJobs listings.json); the content is
    short and made up.
"""

import json

import httpx

from backend.sources.http import HttpClient


def greenhouse_job(job_id, title="Software Engineer Intern", location="Reno, NV",
                   pay_cents=(40_00, 50_00), pay_title="US Hourly Pay"):
    return {
        "absolute_url": f"https://careers.acme.example/positions/{job_id}?gh_jid={job_id}",
        "data_compliance": [{"type": "gdpr", "requires_consent": False}],
        "internal_job_id": 900 + job_id,
        "location": {"name": location},
        "metadata": [],
        "id": job_id,
        "updated_at": "2026-11-11T07:32:56-05:00",
        "requisition_id": "ONE",
        "title": title,
        "company_name": "Acme",
        "first_published": "2026-11-10T04:49:33-05:00",
        "language": "en",
        "content": "&lt;div&gt;&lt;p&gt;Build &lt;strong&gt;APIs&lt;/strong&gt; &amp;amp; tools.&lt;/p&gt;"
                   "&lt;ul&gt;&lt;li&gt;Python&lt;/li&gt;&lt;li&gt;SQL&lt;/li&gt;&lt;/ul&gt;&lt;/div&gt;",
        "departments": [{"id": 1, "name": "Engineering"}],
        "offices": [{"id": 2, "name": location, "location": location}],
        "pay_input_ranges": [] if pay_cents is None else [{
            "min_cents": pay_cents[0], "max_cents": pay_cents[1], "currency_type": "USD",
            "title": pay_title, "blurb": "<p>Base pay.</p>"}],
    }


def greenhouse_board(*jobs):
    return {"jobs": list(jobs), "meta": {"total": len(jobs)}}


def lever_posting(job_id, title="Backend Engineer", workplace="remote", salary=None):
    return {
        "additionalPlain": "We sponsor visas.",
        "additional": "<div>We sponsor visas.</div>",
        "categories": {"commitment": "Internship", "location": "Seattle, WA", "team": "Engineering",
                       "allLocations": ["Seattle, WA", "New York, NY"]},
        "createdAt": 1786469891368,
        "descriptionPlain": "A world-changing company.\nYou will build services.",
        "description": "<div>A world-changing company.</div>",
        "id": job_id,
        "lists": [{"text": "What you'll do", "content": "<li>Ship features</li><li>Write tests</li>"}],
        "text": title,
        "country": "US",
        "workplaceType": workplace,
        "hostedUrl": f"https://jobs.lever.co/acme/{job_id}",
        "applyUrl": f"https://jobs.lever.co/acme/{job_id}/apply",
        **({"salaryRange": salary} if salary else {}),
    }


def ashby_job(job_id, title=" Software Engineer, Security ", listed=True, remote=True):
    return {
        "id": job_id,
        "title": title,
        "department": "Engineering",
        "team": "Backend",
        "employmentType": "FullTime",
        "location": "New York, NY (HQ)",
        "shouldDisplayCompensationOnJobPostings": True,
        "secondaryLocations": [{"location": "Miami, FL", "address": {"postalAddress": {}}}],
        "publishedAt": "2026-04-07T17:12:35.753+00:00",
        "isListed": listed,
        "isRemote": remote,
        "workplaceType": "Hybrid",
        "address": {"postalAddress": {"addressRegion": "NY", "addressCountry": "USA"}},
        "jobUrl": f"https://jobs.ashbyhq.com/acme/{job_id}",
        "applyUrl": f"https://jobs.ashbyhq.com/acme/{job_id}/application",
        "descriptionHtml": "<h1>About Acme</h1><p>We build things.</p>",
        "descriptionPlain": "ABOUT ACME\n\nWe build things.",
        "compensation": {"compensationTierSummary": "$211.4K – $290.6K • Offers Equity",
                         "scrapeableCompensationSalarySummary": "$211.4K - $290.6K"},
    }


def ashby_board(*jobs):
    return {"jobs": list(jobs), "apiVersion": "1"}


def simplify_item(item_id, url, title="Software Engineering Intern", company="Acme",
                  active=True, locations=("San Jose, CA",)):
    return {
        "source": "Simplify", "category": "Software", "company_name": company, "id": item_id,
        "title": title, "active": active, "terms": ["Summer 2027"], "date_updated": 1795305783,
        "date_posted": 1795046400, "url": url, "locations": list(locations),
        "company_url": f"https://simplify.jobs/c/{company}", "is_visible": True,
        "sponsorship": "Other", "degrees": ["Bachelor's"],
    }


class FakeWeb:
    """
        Routes requests to canned responses. Set `routes[url] = (status, body, headers)`, where body
        is a dict/list (sent as JSON) or a str. Records every request in `requests`.
    """

    def __init__(self):
        self.routes = {}
        self.requests = []

    def handler(self, request):
        self.requests.append(request)
        url = str(request.url)
        if url not in self.routes:
            return httpx.Response(404, text="Not Found")
        status, body, headers = self.routes[url]
        etag = (headers or {}).get("etag")
        if etag and request.headers.get("if-none-match") == etag:
            return httpx.Response(304, headers=headers)
        if isinstance(body, (dict, list)):
            body = json.dumps(body)
        return httpx.Response(status, text=body, headers=headers or {})

    def client(self, **kwargs):
        kwargs.setdefault("min_host_interval", 0)
        kwargs.setdefault("requests_per_minute", 0)
        return HttpClient(transport=httpx.MockTransport(self.handler), **kwargs)


GH_FEED = "https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true&pay_transparency=true"
GH_BOARD = "https://boards-api.greenhouse.io/v1/boards/acme"
LEVER_FEED = "https://api.lever.co/v0/postings/acme?mode=json"
ASHBY_FEED = "https://api.ashbyhq.com/posting-api/job-board/acme?includeCompensation=true"
SIMPLIFY_FEED = ("https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/"
                 ".github/scripts/listings.json")
