"""Best-effort live fetch of a job posting's page text: fetch_job_text
refreshes the skills used for an on-demand "Tailor Resume" click (see
src/dashboard/app.py's POST /api/applications/{job_id}/tailor);
fetch_job_posting additionally guesses a title/company, used by the
"Add Job by URL" flow (POST /api/jobs) to create a new Application from
nothing but a pasted URL.

Plain HTTP GET + BeautifulSoup text extraction — no Playwright/browser
here, deliberately: this needs to run from a dashboard request (including
on the AWS Lambda dashboard, which can't run a browser), not just as part
of the local scrape pipeline. That means it does NOT reliably get past
LinkedIn/Indeed's JS rendering or login walls the way the real Playwright
scraper (src/scrapers/, local-only) does — this is fine, because the
tailoring engine always has a solid baseline regardless: whatever skills
the real scraper already found at scrape time
(Application.required_skills) are never discarded, this only adds
anything extra a fresh fetch turns up. A blocked/failed/timed-out fetch
degrades to "no extra signal found" — never an error, never blocks
tailoring.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from typing import NamedTuple, Optional
from urllib.parse import urlparse

from bs4 import BeautifulSoup

# A realistic desktop-browser UA. Not trying to evade detection (this
# isn't scraping to apply, just a one-off read of one already-known URL a
# human clicked "Tailor" on) - just avoiding an instant 403 from sites
# that block obviously-non-browser default User-Agents like "Python-urllib/3.x".
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
_MAX_BYTES = 2_000_000  # plenty for a job posting page; caps a runaway response


def _fetch_html(url: str, timeout: float) -> Optional[bytes]:
    """Shared low-level GET behind both fetch_job_text and
    fetch_job_posting — same best-effort/never-raises contract as
    fetch_job_text's own docstring describes, just factored out so
    neither caller has to duplicate the request/error-handling boilerplate."""
    try:
        request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                return None
            content_type = response.headers.get("Content-Type", "")
            if "html" not in content_type.lower():
                return None
            return response.read(_MAX_BYTES)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None


def fetch_job_text(url: str, timeout: float = 8.0) -> Optional[str]:
    """Returns extracted visible text from `url`, or None on any failure
    (blocked, timeout, non-200, non-HTML, redirected to a login wall that
    still returns 200, etc.) — always best-effort, never raises."""
    raw = _fetch_html(url, timeout)
    if raw is None:
        return None
    try:
        soup = BeautifulSoup(raw, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        text = soup.get_text(separator=" ", strip=True)
        return text or None
    except Exception:
        return None


class JobPosting(NamedTuple):
    text: str  # same extracted visible text fetch_job_text would return
    title_guess: Optional[str]  # best-effort, see _clean_page_title - always editable, never trusted as exact
    company_guess: str  # falls back to the URL's own hostname - always available, so never None


# Trailing "| SiteName" / "- SiteName" segments common in job board <title>
# tags get stripped ONLY when the tail matches one of these - conservative
# on purpose, so a real title that happens to contain a hyphen (e.g.
# "Full-Stack Developer") is never mistaken for one and chopped.
_KNOWN_JOB_SITE_NAMES = (
    "linkedin", "indeed", "glassdoor", "simplyhired", "greenhouse",
    "lever", "workday", "ziprecruiter", "monster", "careerbuilder",
)
_TITLE_SEPARATORS = (" | ", " - ", " – ", " — ")


def _clean_page_title(raw_title: str) -> str:
    """Collapses whitespace and strips a trailing "| LinkedIn"/"- Indeed"
    style suffix, IF the tail after the last known separator matches one
    of _KNOWN_JOB_SITE_NAMES. Never invents or reorders anything else -
    this is a cleanup pass, not a parser, since a generic job posting page
    can format its <title> essentially any way at all."""
    text = " ".join(raw_title.split())
    for sep in _TITLE_SEPARATORS:
        if sep in text:
            head, _, tail = text.rpartition(sep)
            if head and any(site in tail.lower() for site in _KNOWN_JOB_SITE_NAMES):
                text = head
    return text.strip()


def _guess_company_from_url(url: str) -> str:
    """A job posting's <title> tag essentially never reliably contains the
    company name in a generically-parseable spot (real extraction needs
    site-specific selectors, out of scope for a generic best-effort
    fetch) - the URL's own hostname is the one honest fallback that's
    ALWAYS available, so Application.company (a required field) never
    ends up guessing something actively wrong."""
    host = (urlparse(url).netloc or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host or "Unknown"


def fetch_job_posting(url: str, timeout: float = 8.0) -> Optional[JobPosting]:
    """Like fetch_job_text, but also returns a best-effort guessed job
    title (from the page's <title> tag, cleaned via _clean_page_title) and
    company (see _guess_company_from_url) — used only by the dashboard's
    "Add Job by URL" flow (POST /api/jobs in src/dashboard/app.py) to
    pre-fill a new Application without asking the operator to type the
    title/company by hand. Both guesses are meant to be good-enough
    defaults, never authoritative — same "no reliable structured data in
    a generic page" limitation fetch_job_text's own module docstring
    already describes for LinkedIn/Indeed's JS-rendered listings.
    Returns None under the exact same failure conditions as
    fetch_job_text (never raises)."""
    raw = _fetch_html(url, timeout)
    if raw is None:
        return None
    try:
        soup = BeautifulSoup(raw, "html.parser")
        title_guess = (
            _clean_page_title(soup.title.string) if soup.title and soup.title.string else None
        )
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        text = soup.get_text(separator=" ", strip=True)
    except Exception:
        return None
    if not text:
        return None
    return JobPosting(text=text, title_guess=title_guess, company_guess=_guess_company_from_url(url))
