"""End-to-end local pipeline: search every configured source x every
configured search (config/search_criteria.yaml) -> dedupe -> filter
(globally-remote-only, posted within max_age_days) -> tag required
skills/category -> save an Application record (status=pending, no resume
attached) to the configured ApplicationStore.

Deliberately does NOT tailor a resume PDF here — that used to happen
inline for every new job (LLM call and/or reportlab render each time),
which was the slow part of a scrape cycle for zero benefit when most
scraped jobs are never actually applied to. Direct feedback (2026-09-11):
tailor on demand instead, once a job is a real candidate for applying —
the dashboard's per-row "Tailor Resume" button (POST
/api/applications/{job_id}/tailor, built in the Tenth pass) already does
exactly that against src/tailoring/engine.py:tailor_resume_for_job, using
the same original resume template either way. This module just needs to
get out of its way and finish fast.

This is what actually populates the dashboard with real data — until
this has run at least once, the dashboard shows only the local sample
seed data (see src/storage/local_store.py).

Run it locally — NEVER on AWS (see TECHNICAL_PLAN.txt section 6: the
scrapers are a real browser hitting LinkedIn/Indeed/SimplyHired, and an
AWS datacenter IP is itself a red flag independent of anything else):

    python -m src.pipeline.ingest

To write directly into the real AWS tables/bucket (rather than the local
JSON store), set STORAGE_BACKEND=aws plus the table/bucket env vars (see
.env.example) before running this — the scrapers still run on your
machine either way (and so does this whole pipeline — "live" only ever
describes where the dashboard reads results FROM, not where scraping
runs), only the *storage* destination changes.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

import yaml

from src.common.dedupe import SeenJobsCache
from src.common.job_schema import Application, ApplicationStatus
from src.common.location_filters import is_globally_remote
from src.common.skills import extract_skills
from src.pipeline import status as pipeline_status
from src.scrapers.base import human_delay
from src.scrapers.indeed import search_indeed
from src.scrapers.linkedin import search_linkedin
from src.scrapers.simplyhired import search_simplyhired
from src.storage import get_store

CONFIG_PATH = Path("config/search_criteria.yaml")

# Every scraper shares the same (query, location, max_results,
# fetch_descriptions, headless) call shape — see each module's search_*()
# — so one search entry can run against any subset of these by name. New
# source added later: implement search_<name>(...) the same way, add it
# here, and it's immediately usable from config/search_criteria.yaml's
# `sources` list with no other code change.
SOURCE_SCRAPERS: dict[str, Callable] = {
    "simplyhired": search_simplyhired,
    "indeed": search_indeed,
    "linkedin": search_linkedin,
}


def run(log: Optional[Callable[[str], None]] = None) -> dict:
    """Runs every configured search once. `log`, if given, receives each
    progress line (in addition to it always being written to
    data/ingest_log.txt via src.pipeline.status) — used by the
    dashboard's background-thread trigger to avoid a second log sink."""

    def emit(line: str) -> None:
        print(line)
        pipeline_status.append_log(line)
        if log:
            log(line)

    config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    max_results = config.get("max_results_per_search", 8)
    searches = config.get("searches", [])
    remote_only = config.get("remote_only", True)
    max_age_days = config.get("max_age_days")  # None = no age filter

    # Default to simplyhired-only if a config predates the `sources` key
    # (or omits it) — matches this pipeline's original single-source
    # behavior rather than silently going quiet.
    source_names = config.get("sources") or ["simplyhired"]
    unknown = [s for s in source_names if s not in SOURCE_SCRAPERS]
    if unknown:
        raise ValueError(
            f"config/search_criteria.yaml `sources` lists unknown source(s) {unknown!r} — "
            f"must be a subset of {sorted(SOURCE_SCRAPERS)}"
        )

    # Flatten to (search_entry, source_name) pairs up front so progress
    # reporting (total_searches/completed_searches) reflects the real
    # number of scraper calls this run makes, not just the search-keyword
    # count — e.g. 40 keyword entries x 3 sources = 120 actual searches.
    runs = [(entry, source) for entry in searches for source in source_names]

    store = get_store()
    seen = SeenJobsCache()

    pipeline_status.write_status(
        running=True, started_at=datetime.now(timezone.utc).isoformat(),
        total_searches=len(runs), completed_searches=0,
        total_found=0, total_new=0, total_skipped=0,
    )

    total_found = 0
    total_new = 0
    total_skipped = 0
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)) if max_age_days else None
    try:
        for i, (entry, source_name) in enumerate(runs, start=1):
            query = entry["query"]
            location = entry.get("location", "")
            category = entry.get("category")
            emit(f"[{i}/{len(runs)}] Searching {query!r} ({category}) on {source_name} in {location!r}...")

            # A single source having a bad run (blocked, network hiccup, a
            # selector that broke because the site redesigned) must not
            # abort the other 100+ searches in a full multi-source cycle —
            # log it as zero results for this one search and keep going.
            # search_indeed()/search_linkedin() already swallow the common
            # case (a bot-detection challenge page) internally and return
            # [] rather than raising; this except is the outer backstop
            # for anything else (timeouts, DNS, a genuinely broken
            # selector) that isn't the challenge-page case.
            try:
                jobs = SOURCE_SCRAPERS[source_name](query, location, max_results=max_results)
            except Exception as exc:  # noqa: BLE001 - one source's failure isn't the whole run's failure
                emit(f"  ! {source_name} search failed: {exc} — skipping this search")
                pipeline_status.write_status(completed_searches=i, total_found=total_found, total_new=total_new, total_skipped=total_skipped)
                continue

            total_found += len(jobs)
            emit(f"  -> {len(jobs)} results")

            for job in jobs:
                if seen.is_seen(job.job_id):
                    continue

                is_remote = is_globally_remote(job.location, job.description)
                if remote_only and not is_remote:
                    seen.mark_seen(job.job_id)  # don't re-evaluate it every run
                    total_skipped += 1
                    emit(f"    - skipped (not globally remote): {job.title} @ {job.company}")
                    continue

                if cutoff and job.posted_at and job.posted_at < cutoff:
                    seen.mark_seen(job.job_id)
                    total_skipped += 1
                    age_days = (datetime.now(timezone.utc) - job.posted_at).days
                    emit(f"    - skipped (posted {age_days}d ago, older than {max_age_days}d): {job.title} @ {job.company}")
                    continue

                seen.mark_seen(job.job_id)
                total_new += 1

                # No resume tailoring here on purpose — see this module's
                # docstring. required_skills is still tagged now (cheap,
                # pure text matching, no LLM/PDF work) since it drives the
                # dashboard's skill tags/filtering regardless of whether
                # this job is ever tailored or applied to.
                required_skills = extract_skills(job.description)

                application = Application(
                    job_id=job.job_id,
                    source=job.source,
                    title=job.title,
                    company=job.company,
                    url=job.url,
                    status=ApplicationStatus.PENDING,
                    required_skills=required_skills,
                    is_remote=is_remote,
                    category=category,
                    posted_at=job.posted_at,
                    updated_at=datetime.now(timezone.utc),
                )
                store.save_application(application)
                emit(f"    + {job.title} @ {job.company} — {len(required_skills)} skills tagged")

            pipeline_status.write_status(
                completed_searches=i, total_found=total_found,
                total_new=total_new, total_skipped=total_skipped,
            )
            human_delay(5, 12)  # extra gap between distinct searches

        emit(f"Done. {total_found} found, {total_new} new, {total_skipped} skipped (remote/age filters).")
        pipeline_status.write_status(running=False, finished_at=datetime.now(timezone.utc).isoformat())
    except Exception as exc:  # noqa: BLE001 - surface any failure to the status file/log
        emit(f"ERROR: {exc}")
        pipeline_status.write_status(running=False, error=str(exc), finished_at=datetime.now(timezone.utc).isoformat())
        raise

    return {"total_found": total_found, "total_new": total_new, "total_skipped": total_skipped}


if __name__ == "__main__":
    run()
