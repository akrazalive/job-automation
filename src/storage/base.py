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
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> tuple[list[Application], Optional[str]]:
        """Returns (applications, next_cursor). next_cursor is None when
        there are no more pages."""

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
