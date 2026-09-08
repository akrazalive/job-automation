"""SimplyHired scraper — search + job description extraction.

No login required for search/reading, so this is the lowest-friction
place to prove the search -> normalize -> store pipeline (see
TECHNICAL_PLAN.txt Phase 1).

Selectors are pinned to SimplyHired's `data-testid` attributes (verified
against the live site 2026-09-08 via direct HTML fetch), NOT their
auto-generated Chakra UI/Emotion class names (`css-1abcxyz` etc.) — those
churn on every deploy and would break silently. If SimplyHired redesigns
and data-testid names change, parse_search_results()/parse_job_detail()
will start returning empty/partial results — treat an empty result as
"check the selectors," not "no jobs today."

SimplyHired's own "Apply" button (data-testid="viewJobHeaderFooterApplyButton")
is an outbound redirect (`/out?r=...`) to an external ATS or the
employer's own site in most cases — this module only searches and reads
descriptions. Applying is Phase 6.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import quote_plus, urljoin

from bs4 import BeautifulSoup

from src.common.dedupe import job_id_for
from src.common.job_schema import Job, JobSource
from src.scrapers.base import browser_context, human_delay

BASE_URL = "https://www.simplyhired.com"


@dataclass
class SearchResultRow:
    title: str
    company: str
    location: str
    detail_url: str
    date_text: Optional[str] = None  # raw stamp, e.g. "7d", "20h" — see parse_date_stamp()


def parse_date_stamp(text: Optional[str], now: Optional[datetime] = None) -> Optional[datetime]:
    """Parses SimplyHired's compact relative date stamp ("20h", "7d") into
    an absolute UTC datetime. Verified format against the live site
    2026-09-08 (hours/days only observed; "mo"/"y" handled defensively in
    case older postings use them). Returns None if the format doesn't
    match anything recognized — callers should treat that as "unknown
    date", not "very old"."""
    if not text:
        return None
    text = text.strip().lower()
    now = now or datetime.now(timezone.utc)
    match = re.fullmatch(r"(\d+)\s*(h|d|mo|y)", text)
    if not match:
        return None
    amount, unit = int(match.group(1)), match.group(2)
    if unit == "h":
        return now - timedelta(hours=amount)
    if unit == "d":
        return now - timedelta(days=amount)
    if unit == "mo":
        return now - timedelta(days=amount * 30)
    if unit == "y":
        return now - timedelta(days=amount * 365)
    return None  # pragma: no cover — unreachable given the regex above


def build_search_url(query: str, location: str = "", page: int = 0) -> str:
    params = f"q={quote_plus(query)}"
    if location:
        params += f"&l={quote_plus(location)}"
    if page:
        params += f"&pn={page + 1}"
    return f"{BASE_URL}/search?{params}"


def parse_search_results(html: str) -> list[SearchResultRow]:
    """Pure parsing function — no network/browser involved, so this is
    unit-testable against a saved HTML fixture (see
    tests/fixtures/simplyhired_search.html)."""
    soup = BeautifulSoup(html, "html.parser")
    rows: list[SearchResultRow] = []
    for card in soup.select('[data-testid="searchSerpJob"]'):
        title_el = card.select_one('[data-testid="searchSerpJobTitle"] a')
        company_el = card.select_one('[data-testid="companyName"]')
        location_el = card.select_one('[data-testid="searchSerpJobLocation"]')
        date_el = card.select_one('[data-testid="searchSerpJobDateStamp"]')
        if not title_el or not title_el.get("href"):
            continue  # sponsored/placeholder cards without a real job link
        rows.append(
            SearchResultRow(
                title=title_el.get_text(strip=True),
                company=company_el.get_text(strip=True) if company_el else "Unknown",
                location=location_el.get_text(strip=True) if location_el else "",
                detail_url=urljoin(BASE_URL, title_el["href"]),
                date_text=date_el.get_text(strip=True) if date_el else None,
            )
        )
    return rows


def parse_job_detail(html: str) -> str:
    """Extracts the full job description text from a job detail page."""
    soup = BeautifulSoup(html, "html.parser")
    desc_el = soup.select_one('[data-testid="viewJobBodyJobFullDescriptionContent"]')
    return desc_el.get_text("\n", strip=True) if desc_el else ""


def search_simplyhired(
    query: str,
    location: str = "",
    max_results: int = 25,
    fetch_descriptions: bool = True,
    headless: bool = True,
) -> list[Job]:
    """Runs one search on SimplyHired and returns normalized Job records.
    Every page load goes through human_delay() first — see
    TECHNICAL_PLAN.txt section 6; this is deliberately not built for
    speed."""
    jobs: list[Job] = []
    with browser_context(headless=headless) as context:
        page = context.new_page()
        page.goto(build_search_url(query, location), wait_until="domcontentloaded")
        human_delay()
        rows = parse_search_results(page.content())

        for row in rows[:max_results]:
            description: Optional[str] = None
            if fetch_descriptions:
                human_delay()
                page.goto(row.detail_url, wait_until="domcontentloaded")
                description = parse_job_detail(page.content()) or None

            jobs.append(
                Job(
                    job_id=job_id_for("simplyhired", row.company, row.title, row.detail_url),
                    source=JobSource.SIMPLYHIRED,
                    title=row.title,
                    company=row.company,
                    location=row.location,
                    url=row.detail_url,
                    description=description,
                    posted_at=parse_date_stamp(row.date_text),
                )
            )
        page.close()
    return jobs


if __name__ == "__main__":
    # Manual run: reads config/search_criteria.yaml, runs every listed
    # search, writes results to data/found_jobs_simplyhired.json
    # (git-ignored — local scratch, not a source of truth).
    import json
    from pathlib import Path

    import yaml

    config = yaml.safe_load(Path("config/search_criteria.yaml").read_text(encoding="utf-8"))
    max_results = config.get("max_results_per_search", 25)

    all_jobs: list[Job] = []
    for entry in config.get("searches", []):
        print(f"Searching SimplyHired: {entry['query']!r} in {entry.get('location', '')!r}...")
        found = search_simplyhired(entry["query"], entry.get("location", ""), max_results=max_results)
        print(f"  -> {len(found)} jobs found")
        all_jobs.extend(found)
        human_delay(5, 12)  # extra gap between distinct searches

    out_path = Path("data/found_jobs_simplyhired.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps([j.model_dump(mode="json") for j in all_jobs], indent=2),
        encoding="utf-8",
    )
    print(f"Total {len(all_jobs)} jobs -> {out_path}")
