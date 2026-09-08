"""End-to-end local pipeline: search every configured source
(config/search_criteria.yaml) -> dedupe -> filter (globally-remote-only,
posted within max_age_days) -> tag required skills/category -> generate a
tailored resume PDF (LLM-assisted when ANTHROPIC_API_KEY is set, else
deterministic skill-reorder — see src/tailoring/engine.py) -> save an
Application record (status=pending) to the configured ApplicationStore.

This is what actually populates the dashboard with real data — until
this has run at least once, the dashboard shows only the local sample
seed data (see src/storage/local_store.py).

Run it locally — NEVER on AWS (see TECHNICAL_PLAN.txt section 6: the
scraper is a real browser hitting LinkedIn/Indeed/SimplyHired, and an AWS
datacenter IP is itself a red flag independent of anything else):

    python -m src.pipeline.ingest

To write directly into the real AWS tables/bucket (rather than the local
JSON store), set STORAGE_BACKEND=aws plus the table/bucket env vars (see
.env.example) before running this — the scraper still runs on your
machine either way, only the *storage* destination changes.
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
from src.scrapers.simplyhired import search_simplyhired
from src.storage import get_store
from src.tailoring.engine import tailor_resume_for_job

CONFIG_PATH = Path("config/search_criteria.yaml")


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

    store = get_store()
    seen = SeenJobsCache()

    pipeline_status.write_status(
        running=True, started_at=datetime.now(timezone.utc).isoformat(),
        total_searches=len(searches), completed_searches=0,
        total_found=0, total_new=0, total_skipped=0,
    )

    total_found = 0
    total_new = 0
    total_skipped = 0
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)) if max_age_days else None
    try:
        for i, entry in enumerate(searches, start=1):
            query = entry["query"]
            location = entry.get("location", "")
            category = entry.get("category")
            emit(f"[{i}/{len(searches)}] Searching {query!r} ({category}) in {location!r}...")

            jobs = search_simplyhired(query, location, max_results=max_results)
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

                required_skills = extract_skills(job.description)
                tailoring = tailor_resume_for_job(
                    job.job_id, job.title, job.description, required_skills
                )

                application = Application(
                    job_id=job.job_id,
                    source=job.source,
                    title=job.title,
                    company=job.company,
                    url=job.url,
                    status=ApplicationStatus.PENDING,
                    resume_s3_key=tailoring.get("s3_key"),
                    required_skills=required_skills,
                    is_remote=is_remote,
                    category=category,
                    posted_at=job.posted_at,
                    updated_at=datetime.now(timezone.utc),
                )
                store.save_application(application)
                tailor_note = "LLM-tailored" if tailoring.get("llm_tailored") else "skill-reorder only"
                emit(
                    f"    + {job.title} @ {job.company} — {len(required_skills)} skills tagged, {tailor_note}"
                )

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
