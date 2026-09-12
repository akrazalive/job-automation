"""End-to-end local pipeline: search every configured source x every
configured search (config/search_criteria.yaml) -> dedupe -> filter
(remote-only for SimplyHired; remote OR onsite in an allowed country for
Indeed/LinkedIn/Twine — see src/common/location_filters.py:ALLOWED_ONSITE_COUNTRIES
— plus a posting-age check for all sources) -> tag required skills/
category -> save an Application record (status=pending, no resume
attached) to the configured ApplicationStore.

TIERED AGE WINDOW + PER-RUN CAP (direct feedback 2026-09-12: "get me only
jobs from the last 24 hrs... first search in first 24 hrs then first 48
hrs then 72. no need to get 4 days back" + "initially I want you to
scrape just 20 jobs"): when config/search_criteria.yaml sets
`age_tier_hours` (e.g. [24, 48, 72]), run() below no longer saves a job
the moment it's found. Every eligible job across the WHOLE run is instead
bucketed by which age tier its posted_at falls in (an unknown posted_at —
e.g. every Indeed job, see src/scrapers/indeed.py — sorts into its own
lowest-priority trailing bucket rather than being either always-kept or
always-dropped), and ONLY after every configured search has run (or Stop
was requested) are buckets drained newest-tier-first, up to
`max_new_jobs_per_run`, to decide what actually gets saved. Anything
older than the LAST configured tier is dropped outright (never "get 4
days back"); anything that would've been a good candidate but didn't fit
under the cap is simply left unmarked-seen, so a later run can still pick
it up. This is deliberately ONE scrape pass with post-hoc SELECTION, not
three separate re-scrapes at three different age windows — re-querying
the same site for the same keyword returns the same top results
regardless of an age window none of these four sources reliably exposes
a verified URL parameter for (see src/scrapers/twine.py's own docstring
for a concrete example), so a second or third pass would just re-fetch
and re-filter identical data at 2-3x the request cost/bot-detection
exposure for zero actual behavior difference.

A config that sets the legacy `max_age_days` instead (or neither key) get
this module's ORIGINAL behavior unchanged: a flat cutoff, decided and
saved immediately per job as it's found, every source has always worked
this way and any config predating 2026-09-12 keeps working exactly as it
did before this note was added.

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
from src.common.location_filters import is_globally_remote, matched_onsite_country
from src.common.skills import extract_skills
from src.pipeline import status as pipeline_status
from src.scrapers.base import human_delay
from src.scrapers.indeed import search_indeed
from src.scrapers.linkedin import search_linkedin
from src.scrapers.simplyhired import search_simplyhired
from src.scrapers.twine import search_twine
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
    "twine": search_twine,
}


def run(log: Optional[Callable[[str], None]] = None) -> dict:
    """Runs every configured search once. `log`, if given, receives each
    progress line (in addition to it always being written to
    data/ingest_log.txt via src.pipeline.status) — used by the
    dashboard's background-thread trigger to avoid a second log sink."""

    def emit(line: str) -> None:
        # print() goes to a real console, which on this project's target
        # platform (Windows, run via `python -m src.pipeline.ingest` in a
        # plain terminal - see TECHNICAL_PLAN.txt section 6) is very often
        # still the legacy cp1252 codepage, not UTF-8. Caught live
        # 2026-09-11: a checkmark/cross (U+2713/U+2717) in a line here
        # raised UnicodeEncodeError and crashed the whole run. Em dashes
        # and ellipses (—, …) are fine (both are in cp1252); stick to
        # ASCII "+"/"-"/"!" markers for anything else, matching the style
        # already used throughout this function, rather than reaching for
        # a Unicode symbol that looks nicer on this dev machine's own
        # UTF-8 terminal but breaks on the platform this actually ships to.
        print(line)
        pipeline_status.append_log(line)
        if log:
            log(line)

    config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    max_results = config.get("max_results_per_search", 8)
    searches = config.get("searches", [])
    remote_only = config.get("remote_only", True)
    max_age_days = config.get("max_age_days")  # None = no age filter (legacy flat-cutoff mode)

    # Tiered mode (see this module's docstring) activates only when
    # age_tier_hours is actually set — a config that only sets the legacy
    # max_age_days (or neither) keeps the original flat-cutoff,
    # save-immediately behavior byte-for-byte.
    age_tier_hours: Optional[list[int]] = config.get("age_tier_hours")
    tiered_mode = bool(age_tier_hours)
    if tiered_mode:
        age_tier_hours = sorted(age_tier_hours)
    max_new_jobs_per_run = config.get("max_new_jobs_per_run")  # None = unlimited

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
    # count — e.g. 10 keyword entries x 3 sources = 30 actual searches
    # with the current config/search_criteria.yaml.
    runs = [(entry, source) for entry in searches for source in source_names]

    store = get_store()
    seen = SeenJobsCache()
    pipeline_status.clear_stop()  # a Stop click from a PREVIOUS run must never leak into this one

    pipeline_status.write_status(
        running=True, started_at=datetime.now(timezone.utc).isoformat(),
        total_searches=len(runs), completed_searches=0,
        total_found=0, total_new=0, total_skipped=0,
    )

    total_found = 0
    total_new = 0
    total_skipped = 0
    stopped_early = False
    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=max_age_days)) if (max_age_days and not tiered_mode) else None
    # Tiered mode only - the hard, absolute "no need to get 4 days back"
    # boundary (the LAST/oldest configured tier); jobs older than this
    # are dropped outright, same as the legacy cutoff above, just at a
    # different granularity (hours, not days) and only ever active
    # alongside the bucketed candidates list below rather than an
    # immediate per-job save.
    tier_cutoff = (now - timedelta(hours=age_tier_hours[-1])) if tiered_mode else None
    # Tiered mode only - every eligible job found this run, held here
    # (NOT saved, NOT marked seen) until every configured search has run
    # (or Stop fires) - see the selection pass after the loop below and
    # this module's docstring for why. Each entry is
    # (tier_bucket, discovery_order, job_id, application_kwargs);
    # discovery_order only exists so the final sort is stable (ties
    # within a bucket keep first-found order) since Python's sort key
    # can't compare the dict/Job objects directly.
    candidates: list[tuple[int, int, str, dict]] = []
    try:
        for i, (entry, source_name) in enumerate(runs, start=1):
            # Checked once per search (not mid-search) - a Playwright page
            # load in flight isn't interrupted, so "Stop" takes effect at
            # the next search boundary rather than literally mid-request;
            # in practice that's at most the tens of seconds one search
            # (a handful of page loads) takes, not the rest of the run.
            if pipeline_status.should_stop():
                emit(f"Stop requested — halting after {i - 1}/{len(runs)} searches.")
                stopped_early = True
                break

            query = entry["query"]
            category = entry.get("category")
            # Only SimplyHired's search is scoped to the entry's location
            # text (normally "Remote") - Indeed/LinkedIn deliberately
            # search with NO location filter in the query itself (see
            # SOURCE_SEARCH_LOCATION below), so onsite postings in
            # ALLOWED_ONSITE_COUNTRIES actually have a chance to surface
            # in results instead of being scoped out by the query before
            # this module ever sees them; which ones to KEEP is decided
            # per-job below instead.
            location = entry.get("location", "") if source_name == "simplyhired" else ""
            emit(f"[{i}/{len(runs)}] Searching {query!r} ({category}) on {source_name}{f' in {location!r}' if location else ''}...")

            # A single source having a bad run (blocked, network hiccup, a
            # selector that broke because the site redesigned) must not
            # abort the other searches in a multi-source cycle — log it as
            # zero results for this one search and keep going.
            # search_indeed()/search_linkedin() already swallow the common
            # case (a bot-detection challenge page) internally and return
            # [] rather than raising; this except is the outer backstop
            # for anything else (timeouts, DNS, a genuinely broken
            # selector) that isn't the challenge-page case.
            try:
                jobs = SOURCE_SCRAPERS[source_name](query, location, max_results=max_results)
            except Exception as exc:  # noqa: BLE001 - one source's failure isn't the whole run's failure
                emit(f"  {source_name}: ! search failed: {exc} — skipping this search")
                pipeline_status.write_status(completed_searches=i, total_found=total_found, total_new=total_new, total_skipped=total_skipped)
                continue

            total_found += len(jobs)
            emit(f"  {source_name}: -> {len(jobs)} results")

            for job in jobs:
                emit(f"  {source_name}: retrieved \"{job.title}\" @ {job.company}" + (f" ({job.location})" if job.location else ""))

                if seen.is_seen(job.job_id):
                    emit(f"  {source_name}: - already seen — skipped")
                    continue

                is_remote = is_globally_remote(job.location, job.description)
                if not remote_only:
                    allowed, match_note = True, ("remote" if is_remote else (job.location or "location unknown"))
                elif source_name == "simplyhired":
                    emit(f"  {source_name}: checking remote eligibility…")
                    allowed, match_note = is_remote, "remote"
                else:
                    emit(f"  {source_name}: checking remote/country eligibility…")
                    country = None if is_remote else matched_onsite_country(job.location, job.description)
                    allowed, match_note = (is_remote or bool(country)), ("remote" if is_remote else country)

                if not allowed:
                    seen.mark_seen(job.job_id)  # don't re-evaluate it every run
                    total_skipped += 1
                    emit(f"  {source_name}: - skipped — not remote and not in an allowed onsite country")
                    continue

                # No resume tailoring here on purpose — see this module's
                # docstring. required_skills is still tagged now (cheap,
                # pure text matching, no LLM/PDF work) since it drives the
                # dashboard's skill tags/filtering regardless of whether
                # this job is ever tailored or applied to.
                required_skills = extract_skills(job.description)
                app_kwargs = dict(
                    job_id=job.job_id,
                    source=job.source,
                    title=job.title,
                    company=job.company,
                    url=job.url,
                    status=ApplicationStatus.PENDING,
                    required_skills=required_skills,
                    is_remote=is_remote,
                    location=job.location,
                    category=category,
                    posted_at=job.posted_at,
                )

                if not tiered_mode:
                    if cutoff and job.posted_at and job.posted_at < cutoff:
                        seen.mark_seen(job.job_id)
                        total_skipped += 1
                        age_days = (datetime.now(timezone.utc) - job.posted_at).days
                        emit(f"  {source_name}: - skipped — posted {age_days}d ago (older than {max_age_days}d)")
                        continue

                    seen.mark_seen(job.job_id)
                    total_new += 1
                    store.save_application(Application(**app_kwargs, updated_at=datetime.now(timezone.utc)))
                    emit(f"  {source_name}: + {match_note} — saving to Applications ({len(required_skills)} skills tagged)")
                    continue

                # Tiered mode: drop outright anything older than the last
                # configured tier ("no need to get 4 days back") - same
                # "mark seen so it's never re-evaluated" treatment as any
                # other permanent disqualification above.
                if job.posted_at and job.posted_at < tier_cutoff:
                    seen.mark_seen(job.job_id)
                    total_skipped += 1
                    age_hours = (now - job.posted_at).total_seconds() / 3600
                    emit(f"  {source_name}: - skipped — posted {age_hours:.0f}h ago (older than {age_tier_hours[-1]}h)")
                    continue

                if job.posted_at is not None:
                    age_hours = (now - job.posted_at).total_seconds() / 3600
                    bucket = next(idx for idx, hours in enumerate(age_tier_hours) if age_hours <= hours)
                else:
                    # Unknown posting date (e.g. every Indeed job — see
                    # src/scrapers/indeed.py) - kept, not dropped, same as
                    # the legacy flat-cutoff's convention, but as the
                    # LOWEST-priority bucket now rather than always-kept:
                    # it only fills a cap slot once every job with a
                    # confirmed-recent date already has one.
                    bucket = len(age_tier_hours)
                candidates.append((bucket, len(candidates), job.job_id, app_kwargs))
                emit(f"  {source_name}: ~ candidate (age tier {bucket + 1}/{len(age_tier_hours) + 1}) — held for final selection")

            pipeline_status.write_status(
                completed_searches=i, total_found=total_found,
                total_new=total_new, total_skipped=total_skipped,
            )

            # Tiered mode only - once the freshest tier alone already has
            # enough candidates to fill the whole cap, no further search
            # this run can possibly improve the final selection (see the
            # selection pass below - ties within a bucket are arbitrary
            # either way), so stop searching early rather than spending
            # more requests/bot-detection exposure for a result that
            # can't change. Not the same as a user-requested Stop
            # (stopped_early stays False - this is a normal, intentional
            # early finish, not an interruption).
            if tiered_mode and max_new_jobs_per_run is not None:
                tier0_count = sum(1 for c in candidates if c[0] == 0)
                if tier0_count >= max_new_jobs_per_run:
                    emit(
                        f"Found {tier0_count} candidates within the freshest "
                        f"{age_tier_hours[0]}h window (target was {max_new_jobs_per_run}) "
                        "— stopping early, no need to search older tiers."
                    )
                    break

            human_delay(5, 12)  # extra gap between distinct searches

        if tiered_mode:
            # Drain buckets newest-tier-first, stable within a tier (see
            # `candidates`'s own comment) - this is the only place a
            # tiered-mode job actually gets marked seen/saved/counted.
            candidates.sort(key=lambda c: (c[0], c[1]))
            selected = candidates if max_new_jobs_per_run is None else candidates[:max_new_jobs_per_run]
            held_back = len(candidates) - len(selected)
            for bucket, _, job_id, app_kwargs in selected:
                seen.mark_seen(job_id)
                total_new += 1
                store.save_application(Application(**app_kwargs, updated_at=datetime.now(timezone.utc)))
                emit(
                    f"  + saved {app_kwargs['title']!r} @ {app_kwargs['company']} "
                    f"(age tier {bucket + 1}/{len(age_tier_hours) + 1}, {len(app_kwargs['required_skills'])} skills tagged)"
                )
            if held_back:
                # Deliberately NOT marked seen and NOT counted in
                # total_skipped (that counter means "permanently
                # disqualified" - see this function's docstring) - these
                # remain fully eligible for a future run that has more
                # cap room.
                emit(
                    f"{held_back} additional eligible job(s) found but held back — the "
                    f"{max_new_jobs_per_run}-job cap for this run was already reached; "
                    "they remain eligible for the next scrape."
                )
            pipeline_status.write_status(total_found=total_found, total_new=total_new, total_skipped=total_skipped)

        if stopped_early:
            emit(f"Stopped. {total_found} found, {total_new} new, {total_skipped} skipped so far.")
        else:
            emit(f"Done. {total_found} found, {total_new} new, {total_skipped} skipped (remote/country/age filters).")
        pipeline_status.write_status(running=False, finished_at=datetime.now(timezone.utc).isoformat(), stop_requested=False)
    except Exception as exc:  # noqa: BLE001 - surface any failure to the status file/log
        emit(f"ERROR: {exc}")
        pipeline_status.write_status(running=False, error=str(exc), finished_at=datetime.now(timezone.utc).isoformat(), stop_requested=False)
        raise

    return {"total_found": total_found, "total_new": total_new, "total_skipped": total_skipped, "stopped_early": stopped_early}


if __name__ == "__main__":
    run()
