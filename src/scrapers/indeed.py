"""Indeed scraper — search + job description extraction.

Selectors verified against the live site 2026-09-11 via a real headless
Playwright request (not guessed from memory/docs — see
src/scrapers/simplyhired.py's docstring for why that distinction matters
here). Card container is `div.job_seen_beacon`; title/link live in
`h3.jobTitle a[data-jk]` (the `data-jk` attribute is Indeed's own job key,
used to build a canonical `/viewjob?jk=...` detail URL rather than the
`/rc/clk?...` tracking-redirect href the card itself links to); company is
`span[data-testid="company-name"]`; location is
`div[data-testid="text-location"]`.

IMPORTANT, verified live in the same session: Indeed's bot detection is
much more aggressive than SimplyHired's or LinkedIn's guest search — the
very first request in a fresh browser context got a real 200 with real
job cards, and the very next request (seconds later, same context) got a
403 "Security Check" interstitial instead. See
src/scrapers/base.py:looks_blocked(). This module treats a block as an
expected, non-fatal per-request outcome: it logs and returns whatever it
already has rather than raising, so one blocked source never aborts the
rest of a multi-source ingest run. Don't mistake "returned fewer jobs
than usual" for a parser bug without checking the log for a block
message first.

No reliable "posted X days ago" selector was found on the search-results
DOM (the relative-date text that appears elsewhere on the page lives
inside an embedded JSON blob, not a stable CSS-selectable element) — so
Job.posted_at is always None here. That's consistent with how an
unparseable SimplyHired date stamp is already handled elsewhere in this
pipeline: kept, not dropped, and not treated as "very old" by the
max_age_days filter.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from src.common.dedupe import job_id_for
from src.common.job_schema import Job, JobSource
from src.scrapers.base import browser_context, human_delay, looks_blocked

BASE_URL = "https://www.indeed.com"


@dataclass
class SearchResultRow:
    title: str
    company: str
    location: str
    detail_url: str
    date_text: Optional[str] = None  # always None for now — see module docstring


def build_search_url(query: str, location: str = "", page: int = 0) -> str:
    params = f"q={quote_plus(query)}"
    if location:
        params += f"&l={quote_plus(location)}"
    if page:
        params += f"&start={page * 10}"
    return f"{BASE_URL}/jobs?{params}"


def parse_search_results(html: str) -> list[SearchResultRow]:
    """Pure parsing function — no network/browser involved, so this is
    unit-testable against a saved HTML fixture (see
    tests/fixtures/indeed_search.html)."""
    soup = BeautifulSoup(html, "html.parser")
    rows: list[SearchResultRow] = []
    for card in soup.select("div.job_seen_beacon"):
        title_el = card.select_one("h3.jobTitle a")
        company_el = card.select_one('[data-testid="company-name"]')
        location_el = card.select_one('[data-testid="text-location"]')
        if not title_el or not title_el.get("data-jk"):
            continue  # sponsored/placeholder cards without a real job key
        rows.append(
            SearchResultRow(
                title=title_el.get_text(strip=True),
                company=company_el.get_text(strip=True) if company_el else "Unknown",
                location=location_el.get_text(strip=True) if location_el else "",
                detail_url=f"{BASE_URL}/viewjob?jk={title_el['data-jk']}",
            )
        )
    return rows


def parse_job_detail(html: str) -> str:
    """Extracts the full job description text from a job detail page."""
    soup = BeautifulSoup(html, "html.parser")
    desc_el = soup.select_one("#jobDescriptionText")
    return desc_el.get_text("\n", strip=True) if desc_el else ""


def search_indeed(
    query: str,
    location: str = "",
    max_results: int = 25,
    fetch_descriptions: bool = True,
    headless: bool = True,
    log: Optional["callable"] = None,
) -> list[Job]:
    """Runs one search on Indeed and returns normalized Job records. Every
    page load goes through human_delay() first, same as
    src/scrapers/simplyhired.py. A bot-challenge page (see
    src/scrapers/base.py:looks_blocked()) at any point stops this search
    and returns whatever was already collected — never raises — so a
    blocked Indeed request never takes down the rest of a multi-source
    ingest run."""

    def emit(line: str) -> None:
        if log:
            log(line)

    jobs: list[Job] = []
    with browser_context(headless=headless) as context:
        page = context.new_page()
        page.goto(build_search_url(query, location), wait_until="domcontentloaded")
        human_delay()
        html = page.content()
        if looks_blocked(html):
            emit("    ! Indeed blocked this search (bot-detection challenge) — skipping")
            page.close()
            return jobs
        rows = parse_search_results(html)

        for row in rows[:max_results]:
            description: Optional[str] = None
            if fetch_descriptions:
                human_delay()
                page.goto(row.detail_url, wait_until="domcontentloaded")
                detail_html = page.content()
                if looks_blocked(detail_html):
                    emit(f"    ! Indeed blocked a job detail fetch ({row.title!r}) — keeping the listing without a description")
                else:
                    description = parse_job_detail(detail_html) or None

            jobs.append(
                Job(
                    job_id=job_id_for("indeed", row.company, row.title, row.detail_url),
                    source=JobSource.INDEED,
                    title=row.title,
                    company=row.company,
                    location=row.location,
                    url=row.detail_url,
                    description=description,
                    posted_at=None,
                )
            )
        page.close()
    return jobs
