"""Storage interface the dashboard (and later the apply engine) talks to.

Two implementations exist:
  - LocalJsonStore  (src/storage/local_store.py) — a JSON file, zero AWS
    setup required. Used for local dashboard development right now.
  - DynamoStore      (src/storage/dynamo_store.py) — DynamoDB + S3, the
    real AWS-free-tier backend, wired up once infra/aws is deployed.

Application code (the dashboard routes, tests) is written against this
interface only, so swapping backends is a STORAGE_BACKEND env var change,
never a code change. See src/storage/__init__.py:get_store().
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from src.common.job_schema import Application, Job


def filter_records(
    records: list[dict],
    status: Optional[str] = None,
    source: Optional[str] = None,
    company: Optional[str] = None,
    category: Optional[str] = None,
    title: Optional[str] = None,
    search: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> list[dict]:
    """The one filter chain both backends apply, over plain dicts (not yet
    parsed into Application models) — shared here so LocalJsonStore and
    DynamoStore can't drift out of sync the way they briefly did before
    the Eleventh pass unified them, and so list_applications() and
    count_applications() (added for numbered pagination — see that
    method's docstring) apply IDENTICAL filtering, not a copy that could
    silently diverge. See ApplicationStore.list_applications for what
    each filter kwarg means."""
    if status:
        records = [r for r in records if r.get("status") == status]
    if source:
        records = [r for r in records if r.get("source") == source]
    if company:
        records = [r for r in records if company.lower() in (r.get("company") or "").lower()]
    if category:
        records = [r for r in records if (r.get("category") or "").lower() == category.lower()]
    if title:
        records = [r for r in records if title.lower() in (r.get("title") or "").lower()]
    if search:
        needle = search.lower()
        records = [
            r for r in records
            if needle in (r.get("title") or "").lower()
            or needle in (r.get("company") or "").lower()
            or any(needle in s.lower() for s in r.get("required_skills", []))
        ]
    if date_from:
        records = [r for r in records if (r.get("applied_at") or r.get("updated_at", "")) >= date_from]
    if date_to:
        records = [r for r in records if (r.get("applied_at") or r.get("updated_at", "")) <= date_to]
    return records


class ApplicationStore(ABC):
    @abstractmethod
    def list_applications(
        self,
        status: Optional[str] = None,
        source: Optional[str] = None,
        company: Optional[str] = None,
        category: Optional[str] = None,
        title: Optional[str] = None,
        search: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> tuple[list[Application], Optional[str]]:
        """Returns (applications, next_cursor). next_cursor is None when
        there are no more pages.

        category is an exact match (it's a small closed set - the same
        config/search_criteria.yaml categories get_category_breakdown()
        counts - driven by a dropdown, not free text). title is a
        case-insensitive substring match, same style as the existing
        company filter. search is a broader case-insensitive substring
        match across title OR company OR required_skills combined - the
        general "find anything" box; it's independent of (and can be
        combined with) the more specific filters above.

        CURSOR CONTRACT (both backends, as of the numbered-pagination
        addition): cursor is always the decimal string of the OFFSET into
        the filtered+sorted result list — e.g. "25" for page 3 at
        limit=25 — not an opaque token either backend is free to reshape.
        This was already true of both implementations independently; it's
        now a documented guarantee because the dashboard's numbered page
        buttons compute a target page's cursor directly as
        str(page_index * limit) instead of only ever chaining forward
        through next_cursor values one page at a time."""

    @abstractmethod
    def count_applications(
        self,
        status: Optional[str] = None,
        source: Optional[str] = None,
        company: Optional[str] = None,
        category: Optional[str] = None,
        title: Optional[str] = None,
        search: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
    ) -> int:
        """Count of applications matching the same filters as
        list_applications (everything except limit/cursor, which don't
        apply to a count) — added so the dashboard can render numbered
        page buttons (needs total pages = ceil(count / limit)), not just
        a Prev/Next pair. Must filter identically to list_applications
        for the same arguments - both backends implement this via the
        shared src.storage.base.filter_records() so that's structural,
        not just a convention to remember."""

    @abstractmethod
    def get_summary(self) -> dict:
        """Counts of applications by status, plus "total", e.g.
        {"applied": 12, "failed": 3, "blocked": 1,
         "skipped_duplicate": 5, "pending": 2, "total": 23}
        """

    @abstractmethod
    def get_job(self, job_id: str) -> Optional[Job]:
        ...

    @abstractmethod
    def get_resume_url(self, job_id: str) -> Optional[str]:
        """A link to the tailored resume used for this application (a
        presigned S3 URL in the AWS backend), or None if unavailable."""

    @abstractmethod
    def save_application(self, application: Application) -> None:
        """Create or update (upsert by job_id) one application record.
        Called by the ingest pipeline (src/pipeline/ingest.py) after a
        job is scraped and its resume tailored."""

    @abstractmethod
    def delete_application(self, job_id: str) -> bool:
        """Permanently removes one application record — used by the
        Applications page's bulk "Delete selected" action (a human
        choosing to clean up their own list, not scraping/automation, so
        allowed on both backends like Mark Applied / project-bank CRUD).
        Also best-effort deletes the tailored PDF that application record
        pointed to (local disk file, or the S3 object named by its
        resume_s3_key) — never lets a missing/already-gone file block the
        record delete itself. Returns False if job_id doesn't exist, True
        on success."""

    @abstractmethod
    def delete_all_applications(self) -> int:
        """Permanently removes EVERY application record, plus each one's
        tailored PDF (same best-effort local-disk/S3 cleanup as
        delete_application) — backs the Applications page's "Delete All"
        button (a human wiping their own list, same allowed-because-not-
        automation reasoning as delete_application/mark_applied). Returns
        how many records were deleted."""

    @abstractmethod
    def get_category_breakdown(self) -> dict:
        """Counts per category (config/search_criteria.yaml `category`
        field), e.g. {"frontend": {"total": 12, "applied": 3, ...}, ...}.
        Jobs with no category land under "Uncategorized"."""

    @abstractmethod
    def mark_applied(self, job_id: str) -> bool:
        """Manually marks one application as status=applied — set by a
        human clicking "Mark Applied" on the dashboard, NOT by an apply
        bot (Phase 4-6 doesn't exist yet). Sets applied_at to now.
        Returns False if job_id doesn't exist, True on success."""

    @abstractmethod
    def get_application(self, job_id: str) -> Optional[Application]:
        """Fetches one application by id — used by the "Tailor Resume"
        route to read the job's url/title/required_skills before
        regenerating its PDF. Returns None if job_id doesn't exist."""

    @abstractmethod
    def update_resume_tailoring(
        self,
        job_id: str,
        resume_s3_key: Optional[str],
        tailored_skills: list[str],
        resume_filename: Optional[str] = None,
    ) -> Optional[str]:
        """Records that an on-demand "Tailor Resume" click regenerated
        this job's PDF: sets resume_tailored_at to now, resume_tailored_skills
        to the skills actually used, resume_s3_key when one is given (AWS
        backend only — see src/tailoring/engine.py:tailor_resume_for_job),
        and resume_filename (both backends — the on-disk/S3-key basename,
        e.g. "senior-backend-engineer-20260910153045.pdf") when one is
        given. Returns the ISO timestamp it set resume_tailored_at to (so
        the caller — the dashboard route — can hand it straight back to
        the browser to patch that row in place, without a full table
        reload that could otherwise scroll the just-tailored row onto a
        different page; see TECHNICAL_PLAN.txt's "PDF not updating"
        writeup), or None if job_id doesn't exist."""
