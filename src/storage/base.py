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
        combined with) the more specific filters above."""

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
        self, job_id: str, resume_s3_key: Optional[str], tailored_skills: list[str]
    ) -> Optional[str]:
        """Records that an on-demand "Tailor Resume" click regenerated
        this job's PDF: sets resume_tailored_at to now, resume_tailored_skills
        to the skills actually used, and resume_s3_key when one is given
        (the AWS backend uploads a fresh PDF each time; the local backend
        always writes resume/output/<job_id>.pdf instead, so
        resume_s3_key stays None there — same split as
        src/tailoring/engine.py:tailor_resume_for_job). Returns the ISO
        timestamp it set resume_tailored_at to (so the caller — the
        dashboard route — can hand it straight back to the browser to
        patch that row in place, without a full table reload that could
        otherwise scroll the just-tailored row onto a different page; see
        TECHNICAL_PLAN.txt's "PDF not updating" writeup), or None if
        job_id doesn't exist."""
