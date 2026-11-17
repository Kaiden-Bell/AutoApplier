"""Criteria gate tests: the original fixture cases plus regression cases for the port."""

import json
import os

import pytest

from backend.config import load_default_criteria
from backend.criteria_matcher import eval_job_criteria, parse_pay
from conftest import REPO_ROOT

CRITERIA = load_default_criteria()

with open(os.path.join(REPO_ROOT, "tests", "test_cases.json"), encoding="utf-8") as f:
    FIXTURE_CASES = json.load(f)


@pytest.mark.parametrize("case", FIXTURE_CASES, ids=[c["name"] for c in FIXTURE_CASES])
def test_fixture_cases(case):
    passed, reason = eval_job_criteria(case["payload"], CRITERIA)
    assert passed == case["expected"], reason


@pytest.mark.parametrize("pay, expected", [
    ("$45.00 / hr", (45.0, "hourly")),
    ("$32.50 per hour", (32.5, "hourly")),
    ("Salary: $65,000 / year", (65000.0, "annual")),
    ("$20000 / year", (20000.0, "annual")),          # used to parse as $200/hr
    ("$85k - $105k", (105000.0, "annual")),
    ("$85,000 - $105,000 / year", (105000.0, "annual")),
    ("$4,000 / month", (48000.0, "annual")),
    ("$25", (25.0, "hourly")),
    ("Starting at $35,000", (35000.0, "annual")),
    ("Competitive", None),
    ("", None),
])
def test_parse_pay(pay, expected):
    assert parse_pay(pay) == expected


def job(title="Software Engineer", location="Reno, NV", pay=""):
    return {"title": title, "location": location, "pay": pay}


def test_low_annual_salary_rejected_as_annual():
    passed, reason = eval_job_criteria(job(pay="$20000 / year"), CRITERIA)
    assert not passed
    assert "annual" in reason


def test_banned_token_is_whole_word():
    # "ii" must not match inside "Hawaii"; "lead" must not match "Leadership".
    assert eval_job_criteria(job(title="Software Engineer - Hawaii Office", location="Remote"), CRITERIA)[0]
    assert eval_job_criteria(job(title="Software Leadership Program"), CRITERIA)[0]
    assert not eval_job_criteria(job(title="Software Engineer II"), CRITERIA)[0]


def test_uppercase_target_tokens_match():
    assert eval_job_criteria(job(title="AI Intern"), CRITERIA)[0]


def test_missing_pay_passes():
    assert eval_job_criteria(job(pay=""), CRITERIA)[0]


def test_empty_criteria_lists_are_not_enforced():
    open_criteria = {"target_tokens": [], "banned_tokens": [], "allowed_locations": [],
                     "minimum_hourly_rate": 0, "minimum_annual_salary": 0}
    assert eval_job_criteria(job(title="Barista", location="Anywhere", pay="$1/hr"), open_criteria)[0]


def test_extension_and_backend_default_criteria_match():
    with open(os.path.join(REPO_ROOT, "extension", "criteria.json"), encoding="utf-8") as f:
        assert json.load(f) == CRITERIA
