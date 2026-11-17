"""
    Deterministic criteria gate: title / location / pay checks that run before any LLM call.

    The criteria come from the user's extension settings (or the bundled default criteria.json),
    and the job dict comes from the extension's page extraction.
"""

import re

_HOURLY_RE = re.compile(r"\b(hour|hours|hourly|hr|hrs)\b|/\s*h\b")
_MONTHLY_RE = re.compile(r"\b(month|monthly|mo)\b")
_ANNUAL_RE = re.compile(r"\b(year|years|yearly|annual|annually|yr|yrs|pa)\b")
# A number such as 85,000 / 85000 / 32.50, optionally followed by "k" (thousands).
_AMOUNT_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(k)?\b", re.IGNORECASE)


def _contains_token(text, token):
    """Whole-word / whole-phrase match, so "ii" does not match "hawaii" and "lead" not "leader"."""
    token = token.strip().lower()
    if not token:
        return False
    return re.search(r"(?<!\w)" + re.escape(token) + r"(?!\w)", text) is not None


def parse_pay(pay_text):
    """
        Desc:
            Parses free-text pay such as "$45.00 / hr", "$85k - $105k", "$20000 / year".
        Returns:
            tuple | None: (amount, "hourly" | "annual") using the top of any range, or None if no
            amount is present.
    """
    text = (pay_text or "").lower()
    amounts = []
    for number, k_suffix in _AMOUNT_RE.findall(text):
        try:
            value = float(number.replace(",", ""))
        except ValueError:
            continue
        if k_suffix:
            value *= 1000
        amounts.append(value)

    if not amounts:
        return None

    amount = max(amounts)
    if _HOURLY_RE.search(text):
        return amount, "hourly"
    if _MONTHLY_RE.search(text):
        return amount * 12, "annual"
    if _ANNUAL_RE.search(text):
        return amount, "annual"
    # No unit given: small numbers are hourly rates, large ones are salaries.
    return amount, ("hourly" if amount < 500 else "annual")


def eval_job_criteria(job, criteria):
    """
        Desc:
            Evaluates an extracted job posting against the user's criteria.
        Args:
            job (dict): Extracted posting with "title", "location" and "pay" strings.
            criteria (dict): target_tokens, banned_tokens, allowed_locations,
                minimum_hourly_rate, minimum_annual_salary. Missing keys are not enforced.
        Returns:
            tuple: (bool, str) whether the job passes, and a human-readable reason.
    """
    title = (job.get("title") or "").lower()
    location = (job.get("location") or "").lower()
    pay_text = job.get("pay") or ""

    banned = [t for t in criteria.get("banned_tokens", []) if _contains_token(title, t)]
    if banned:
        return False, f"Job title contains banned token(s): {', '.join(banned)}"

    targets = criteria.get("target_tokens", [])
    if targets and not any(_contains_token(title, t) for t in targets):
        return False, f"Job title does not contain any target token(s): {', '.join(targets)}"

    locations = [loc.strip().lower() for loc in criteria.get("allowed_locations", []) if loc.strip()]
    if locations and not any(loc in location for loc in locations):
        shown = location or "(none given)"
        return False, f"Job location '{shown}' is not in the list of allowed locations: {', '.join(locations)}"

    parsed = parse_pay(pay_text)
    if parsed:
        amount, unit = parsed
        if unit == "hourly":
            minimum = criteria.get("minimum_hourly_rate")
            if minimum and amount < minimum:
                return False, f"Job pay rate ${amount:g}/hr is below the minimum hourly rate of ${minimum:g}/hr"
        else:
            minimum = criteria.get("minimum_annual_salary")
            if minimum and amount < minimum:
                return False, f"Job salary ${amount:,.0f}/yr is below the minimum annual salary of ${minimum:,.0f}/yr"

    return True, "Job meets all criteria"
