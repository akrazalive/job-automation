"""Best-effort filtering for "globally remote" (open to anywhere) vs.
"remote, but restricted to one country/region" postings.

This is text-pattern matching against the location/description, NOT an
authoritative check — a restriction phrased in a way not covered by
RESTRICTION_PATTERNS will slip through uncaught, and a job that
mentions e.g. "US timezone preferred" without an outright restriction
will (correctly) still pass. Treat this as "filters the obvious cases,"
not a guarantee.
"""

from __future__ import annotations

import re
from typing import Optional

RESTRICTION_PATTERNS = [
    r"remote\s*\(?\s*u\.?s\.?\s*only\s*\)?",
    r"remote\s*-\s*u\.?s\.?\b",
    r"u\.?s\.?\s*-\s*based\s*only",
    r"united states only",
    r"must be (located|based|residing) in the (us|united states|u\.s\.)",
    r"must reside in the (us|united states|u\.s\.)",
    r"authorized to work in the (us|united states|u\.s\.) without sponsorship",
    r"(us|u\.s\.) citizens? only",
    r"canada only",
    r"remote\s*\(?\s*canada\s*only\s*\)?",
    r"uk only",
    r"remote\s*\(?\s*uk\s*only\s*\)?",
    r"europe only",
    r"\beu only\b",
    r"must be (located|based) in (canada|the uk|europe)",
]

_COMPILED = [re.compile(p, re.IGNORECASE) for p in RESTRICTION_PATTERNS]


def is_globally_remote(location: Optional[str], description: Optional[str]) -> bool:
    """True if the location text says "remote" and neither the location
    nor description contains a recognized country/region restriction
    phrase. See module docstring for the limits of this check."""
    location = location or ""
    if "remote" not in location.lower():
        return False
    haystack = f"{location}\n{description or ''}"
    return not any(p.search(haystack) for p in _COMPILED)


# Direct request (2026-09-11): besides globally-remote postings, Indeed
# and LinkedIn should also surface ONSITE jobs based in any of these
# countries (SimplyHired stays remote-only — see src/pipeline/ingest.py).
# Order doesn't matter for matching; kept as given. "Saudi Arabia" also
# matches its common short form.
ALLOWED_ONSITE_COUNTRIES = [
    "Pakistan", "Malaysia", "Maldives", "Bahrain", "Qatar", "Kuwait", "Oman",
    "Saudi Arabia", "Azerbaijan", "Armenia", "Lithuania", "Latvia", "Malta",
    "Singapore", "Luxembourg",
]

_COUNTRY_SYNONYMS = {"Saudi Arabia": ["Saudi Arabia", "KSA"]}


def matched_onsite_country(location: Optional[str], description: Optional[str]) -> Optional[str]:
    """Best-effort text match (same "good enough for tagging, not a
    guarantee" standard as is_globally_remote/RESTRICTION_PATTERNS above)
    for whether a job is based in one of ALLOWED_ONSITE_COUNTRIES —
    checked against the location text first (cheap, usually sufficient:
    "Karachi, Pakistan", "Doha, Qatar") and the description as a
    fallback for postings whose location field is vague ("On-site") but
    name the country in the body text. Returns the matched country name
    (from ALLOWED_ONSITE_COUNTRIES, for logging/display) or None."""
    haystack = f"{location or ''}\n{description or ''}".lower()
    for country in ALLOWED_ONSITE_COUNTRIES:
        names = _COUNTRY_SYNONYMS.get(country, [country])
        if any(name.lower() in haystack for name in names):
            return country
    return None
