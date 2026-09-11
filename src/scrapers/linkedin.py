"""LinkedIn scraper — search + job description extraction, guest (no
login) search only.

This is search/read ONLY, same scope as src/scrapers/simplyhired.py —
Phase 4's actual apply automation (login flow, Easy Apply, storage_state
persistence) is a separate, much bigger, not-yet-started piece of work;
nothing here logs in or touches an account.

LinkedIn's public "guest" job search (linkedin.com/jobs/search, no
session/cookies required) is server-rendered HTML — verified live
2026-09-11 via a plain HTTP GET returning a real 200 with ~25 real job
cards, no JS execution needed to see them. Selectors pinned to the real
class names observed then: card container is `div.base-search-card`
(nested in `li.base-search-card__list-item` in the actual site, but this
parser selects directly on `.base-search-card` so it doesn't depend on
that wrapper), title `h3.base-search-card__title`, company
`h4.base-search-card__subtitle`, location
`span.job-search-card__location`, and — unlike SimplyHired's relative
"7d"/"20h" stamps — an absolute ISO date in
`time.job-search-card__listdate[datetime]`, which parse_date_iso() reads
directly with no relative-time math needed.

The detail link (`a.base-card__full-link`) carries a long tracking query
string (`?position=...&pageNum=...&trackingId=...`) that changes across
requests for the same job — stripped down to the bare
`/jobs/view/<slug>-<id>` path here so job_id_for() (which hashes the URL)
stays stable across repeated scrapes of the same posting instead of
minting a "new" job every run.

Guest search has no published rate limit, but it's still LinkedIn — the
same block-detection used for Indeed applies here too (see
src/scrapers/base.py:looks_blocked()), treated as a non-fatal per-request
outcome for the same "log and move on, never abort the whole run" reason.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from src.common.dedupe import job_id_for
from src.common.job_schema import Job, JobSource
from src.scrapers.base import browser_context, human_delay, looks_blocked

BASE_URL = "https://www.linkedin.com"


@dataclass
class SearchResultRow:
    title: str
    company: str
    location: str
    detail_url: str
    date_text: Optional[str] = None  # raw ISO date ("2026-05-28") — see parse_date_iso()


def parse_date_iso(text: Optional[str]) -> Optional[datetime]:
    """Parses the absolute ISO date LinkedIn's guest search puts on
    `time.job-search-card__listdate[datetime]` (e.g. "2026-05-28") into a
    UTC datetime. Unlike SimplyHired's relative stamp, this needs no
    "now" reference at all. Returns None on anything that doesn't match —
    callers should treat that as "unknown date", not "very old", same
    convention as parse_date_stamp() in simplyhired.py."""
    if not text:
        return None
    text = text.strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def build_search_url(query: str, location: str = "", page: int = 0) -> str:
    params = f"keywords={quote_plus(query)}"
    if location:
        params += f"&location={quote_plus(location)}"
    if page:
        params += f"&start={page * 25}"
    return f"{BASE_URL}/jobs/search?{params}"


def parse_search_results(html: str) -> list[SearchResultRow]:
    """Pure parsing function — no network/browser involved, so this is
    unit-testable against a saved HTML fixture (see
    tests/fixtures/linkedin_search.html)."""
    soup = BeautifulSoup(html, "html.parser")
    rows: list[SearchResultRow] = []
    for card in soup.select(".base-search-card"):
        title_el = card.select_one(".base-search-card__title")
        company_el = card.select_one(".base-search-card__subtitle")
        location_el = card.select_one(".job-search-card__location")
        link_el = card.select_one("a.base-card__full-link")
        date_el = card.select_one(".job-search-card__listdate")
        if not link_el or not link_el.get("href") or not title_el:
            continue  # promoted/placeholder cards without a real job link
        rows.append(
            SearchResultRow(
                title=title_el.get_text(strip=True),
                company=company_el.get_text(strip=True) if company_el else "Unknown",
                location=location_el.get_text(strip=True) if location_el else "",
                detail_url=link_el["href"].split("?")[0],  # strip volatile tracking params
                date_text=date_el.get("datetime") if date_el else None,
            )
        )
    return rows


def parse_job_detail(html: str) -> str:
    """Extracts the full job description text from a job detail page."""
    soup = BeautifulSoup(html, "html.parser")
    desc_el = soup.select_one(".show-more-less-html__markup")
    return desc_el.get_text("\n", strip=True) if desc_el else ""


def search_linkedin(
    query: str,
    location: str = "",
    max_results: int = 25,
    fetch_descriptions: bool = True,
    headless: bool = True,
    log: Optional["callable"] = None,
) -> list[Job]:
    """Runs one guest search on LinkedIn and returns normalized Job
    records. Every page load goes through human_delay() first, same as
    src/scrapers/simplyhired.py. A bot-challenge page (see
    src/scrapers/base.py:looks_blocked()) at any point stops this search
    and returns whatever was already collected — never raises."""

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
            emit("    ! LinkedIn blocked this search (bot-detection challenge) — skipping")
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
                    emit(f"    ! LinkedIn blocked a job detail fetch ({row.title!r}) — keeping the listing without a description")
                else:
                    description = parse_job_detail(detail_html) or None

            jobs.append(
                Job(
                    job_id=job_id_for("linkedin", row.company, row.title, row.detail_url),
                    source=JobSource.LINKEDIN,
                    title=row.title,
                    company=row.company,
                    location=row.location,
                    url=row.detail_url,
                    description=description,
                    posted_at=parse_date_iso(row.date_text),
                )
            )
        page.close()
    return jobs
