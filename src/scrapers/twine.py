"""Twine (twine.net, formerly twine.fm) scraper — search + job description
extraction. Search/read only, same scope as the other three scrapers in
this package — no login, no apply.

VERIFIED LIVE 2026-09-12 (via a real headless Playwright session, both the
listing and detail page — not guessed from memory/docs, same discipline
the other scrapers' docstrings describe), with two things that make this
source meaningfully different from simplyhired.py/indeed.py/linkedin.py:

1. The job LISTING page (twine.net/jobs/...) is a client-side-rendered
   React SPA — the initial server HTML has no job cards at all, only
   skeleton loaders; cards only exist in the DOM after the page's own JS
   fetches and hydrates them. `wait_until="domcontentloaded"` (what every
   other scraper here uses) is NOT enough on its own — search_twine()
   below additionally waits for a real `a[href^="/projects/"]` card link
   to appear before reading page.content(). The job DETAIL page, by
   contrast, IS server-rendered (verified: identical HTML before and
   after an extra wait) — its request uses plain domcontentloaded like
   every other scraper's detail fetch.

2. Twine has no working keyword-search URL. `/jobs?keyword=...`,
   `?keywords=`, `?q=`, `?search=`, and `?query=` were all tried live and
   every one returned the exact same unfiltered listing — the visible
   "Enter any keywords..." search box drives client-side React state via
   a button click, not a URL a scraper can just build. What DOES work as
   a plain, bookmarkable URL is Twine's own category browse pages (e.g.
   "/jobs/web-developers", found live in the page's own "In Demand
   Skills"/"Browse more jobs" sidebar links) — CATEGORY_PATH below is
   pinned to that, and build_search_url()/search_twine() deliberately
   IGNORE the `query`/`location` arguments entirely (kept only so this
   still matches every other search_<source>() function's call shape for
   src/pipeline/ingest.py:SOURCE_SCRAPERS). Practical effect: every
   configured search entry hits the exact same Twine URL once per scrape
   cycle — wasted requests, but not wrong results — the pipeline's own
   SeenJobsCache already dedupes the repeat job_ids for free (same
   company+title+url every time), so no duplicate Application ever gets
   saved. If Twine ever exposes a real query param, swap it in here; a
   config-level way to only run one source against a subset of searches
   doesn't exist yet (see config/search_criteria.yaml's own note on this).

Card selectors are pinned to a mix of:
  - The one thing about the listing DOM that's a routing convention, not
    a generated hash, and so is the least likely to break on a Twine
    frontend redeploy: `a[href^="/projects/"]` for the job link itself.
  - CSS-Modules content-hash class names (e.g. "_17QBDj78" for the client
    name, "_1L_9v_yL" for the location/budget/role tag row) for
    everything else — genuinely MORE fragile than the data-testid/BEM
    selectors the other three scrapers pin to, because these hashes are
    exactly the kind of thing that regenerates on every Twine deploy.
    Treat an empty company/location/date (title+link still present) as
    "check these specific selectors first," even more readily than for
    simplyhired.py/indeed.py/linkedin.py.
  - The job DETAIL page's description, by contrast, sits under a real
    schema.org microdata attribute (`itemprop="description"`) — much
    more stable than a hash, used here for exactly that reason.

Client/company name is frequently just absent on Twine's public job
board (most cards show no client name at all, only a role/budget/
location) — parse_search_results() falls back to "Unknown" exactly like
the other three scrapers already do for a missing company element, not a
Twine-specific case.

Twine skews CREATIVE freelance work overall (Animator, Videographer,
Photographer, Voiceover Artist, etc. — see its own "Browse more jobs"
sidebar) — CATEGORY_PATH intentionally targets its "Web Developer Jobs"
category specifically so this source stays relevant to a software-
engineering job search, not Twine's full catalog.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from src.common.dedupe import job_id_for
from src.common.job_schema import Job, JobSource
from src.scrapers.base import browser_context, human_delay, looks_blocked

BASE_URL = "https://www.twine.net"
# See module docstring point 2 - the only real, bookmarkable, filtered
# route this source has for software-dev-relevant postings.
CATEGORY_PATH = "/jobs/web-developers"

# CSS-Modules content-hash class names, verified live 2026-09-12 - see
# module docstring for why these (unlike the rest of this parser) are the
# most likely thing here to break on a Twine redeploy.
_COMPANY_CLASS = "_17QBDj78"
_TAGS_ROW_CLASS = "_1L_9v_yL"
_CARD_CLASS = "_1uiGxoF2"

_RELATIVE_DATE_RE = re.compile(r"(a|an|\d+)\s+(minute|hour|day|week|month|year)s?\s+ago")
_UNIT_SECONDS = {
    "minute": 60, "hour": 3600, "day": 86400,
    "week": 7 * 86400, "month": 30 * 86400, "year": 365 * 86400,
}


@dataclass
class SearchResultRow:
    title: str
    company: str
    location: str
    detail_url: str
    date_text: Optional[str] = None  # raw "N days ago" / "a day ago" — see parse_date_relative()


def parse_date_relative(text: Optional[str], now: Optional[datetime] = None) -> Optional[datetime]:
    """Parses Twine's "Posted a day ago" / "Posted 3 hours ago" style
    relative stamp into an absolute UTC datetime. Same convention as
    simplyhired.py's parse_date_stamp()/linkedin.py's parse_date_iso():
    returns None on anything unrecognized — callers must treat that as
    "unknown date," never "very old.\""""
    if not text:
        return None
    now = now or datetime.now(timezone.utc)
    match = _RELATIVE_DATE_RE.fullmatch(text.strip().lower())
    if not match:
        return None
    amount_raw, unit = match.group(1), match.group(2)
    amount = 1 if amount_raw in ("a", "an") else int(amount_raw)
    return now - timedelta(seconds=amount * _UNIT_SECONDS[unit])


def build_search_url(query: str = "", location: str = "", page: int = 0) -> str:
    """`query`/`location`/`page` are accepted only to match every other
    search_<source>() function's call shape — Twine has no working
    keyword-search URL, so all three are ignored. See module docstring."""
    return f"{BASE_URL}{CATEGORY_PATH}"


def parse_search_results(html: str) -> list[SearchResultRow]:
    """Pure parsing function — no network/browser involved, so this is
    unit-testable against a saved HTML fixture (see
    tests/fixtures/twine_search.html). Expects HTML captured AFTER the
    listing page's own JS has hydrated real job cards in — see
    search_twine()'s wait_for_selector call; the raw server response has
    none of this."""
    soup = BeautifulSoup(html, "html.parser")
    rows: list[SearchResultRow] = []
    seen_hrefs: set[str] = set()

    for anchor in soup.select('a[href^="/projects/"]'):
        href = anchor.get("href")
        if not href or href in seen_hrefs:
            continue
        # Each card links TWO anchors to the same job (a title link
        # wrapping a <div>, and a "Read more" description link that's
        # plain text with no wrapping div) - only the first carries the
        # title, so this also naturally skips the second occurrence.
        title_div = anchor.find("div", recursive=False)
        if title_div is None:
            continue
        title = "".join(title_div.find_all(string=True, recursive=False)).strip()
        if not title:
            continue
        seen_hrefs.add(href)

        card = anchor.find_parent("div", class_=_CARD_CLASS)

        company_el = card.find("div", class_=_COMPANY_CLASS) if card else None

        location_text = ""
        tags_row = card.find("div", class_=_TAGS_ROW_CLASS) if card else None
        if tags_row is not None:
            first_tag = tags_row.find("div", recursive=False)
            if first_tag is not None:
                # Strips the leading location/globe emoji marker (e.g.
                # "🌎 Remote" -> "Remote") - content-based, not tied to
                # any specific emoji, so it survives Twine swapping icons.
                location_text = re.sub(r"^[^\w]+", "", first_tag.get_text(strip=True)).strip()

        date_text = None
        if card is not None:
            for div in card.find_all("div"):
                first_text = div.find(string=True, recursive=False)
                if first_text and first_text.strip().startswith("Posted"):
                    match = re.search(r"Posted\s+(.*?ago)", div.get_text(" ", strip=True))
                    if match:
                        date_text = match.group(1)
                    break

        rows.append(
            SearchResultRow(
                title=title,
                company=company_el.get_text(strip=True) if company_el else "Unknown",
                location=location_text,
                detail_url=urljoin(BASE_URL, href),
                date_text=date_text,
            )
        )
    return rows


def parse_job_detail(html: str) -> str:
    """Extracts the full job description text from a job detail page —
    pinned to the schema.org `itemprop="description"` microdata
    attribute (verified live 2026-09-12), not a CSS-Modules hash, since
    this one element is meaningfully more stable than the listing page's
    selectors — see module docstring."""
    soup = BeautifulSoup(html, "html.parser")
    desc_el = soup.select_one('[itemprop="description"]')
    return desc_el.get_text("\n", strip=True) if desc_el else ""


def search_twine(
    query: str,
    location: str = "",
    max_results: int = 25,
    fetch_descriptions: bool = True,
    headless: bool = True,
    log: Optional["callable"] = None,
) -> list[Job]:
    """Runs one search on Twine and returns normalized Job records.
    `query`/`location` are accepted for call-shape parity with the other
    search_<source>() functions but ignored — see module docstring and
    build_search_url(). Every page load goes through human_delay() first,
    same as the other three scrapers; the listing page additionally waits
    for real job-card HTML to hydrate in (see module docstring point 1)
    before that delay/read, since this source's search page is a
    client-rendered SPA unlike the other three. A bot-challenge page or a
    hydration timeout (see looks_blocked()/the wait_for_selector below)
    stops this search and returns whatever was already collected — never
    raises — so a blocked/slow Twine request never takes down the rest of
    a multi-source ingest run."""

    def emit(line: str) -> None:
        if log:
            log(line)

    jobs: list[Job] = []
    with browser_context(headless=headless) as context:
        page = context.new_page()
        page.goto(build_search_url(), wait_until="domcontentloaded")
        try:
            page.wait_for_selector('a[href^="/projects/"]', timeout=15000)
        except PlaywrightTimeoutError:
            emit("    ! Twine: job listings never hydrated in (slow load or a genuinely empty category) — skipping")
            page.close()
            return jobs
        human_delay()
        html = page.content()
        if looks_blocked(html):
            emit("    ! Twine blocked this search (bot-detection challenge) — skipping")
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
                    emit(f"    ! Twine blocked a job detail fetch ({row.title!r}) — keeping the listing without a description")
                else:
                    description = parse_job_detail(detail_html) or None

            jobs.append(
                Job(
                    job_id=job_id_for("twine", row.company, row.title, row.detail_url),
                    source=JobSource.TWINE,
                    title=row.title,
                    company=row.company,
                    location=row.location,
                    url=row.detail_url,
                    description=description,
                    posted_at=parse_date_relative(row.date_text),
                )
            )
        page.close()
    return jobs


if __name__ == "__main__":
    # Manual run: config/search_criteria.yaml's `searches` entries are
    # read for parity with the other scrapers' own __main__ blocks, but
    # (per this module's whole docstring) every one of them hits the same
    # Twine URL - so this only actually needs to run once. Writes results
    # to data/found_jobs_twine.json (git-ignored - local scratch, not a
    # source of truth).
    import json
    from pathlib import Path

    print("Searching Twine (web-developers category - query/location ignored, see module docstring)...")
    found = search_twine("", "")
    print(f"  -> {len(found)} jobs found")

    out_path = Path("data/found_jobs_twine.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps([j.model_dump(mode="json") for j in found], indent=2),
        encoding="utf-8",
    )
    print(f"Total {len(found)} jobs -> {out_path}")
