"""Shared data models for jobs and applications.

These are the records that flow through the whole pipeline: scrapers
produce Job records, the apply engine produces/updates Application
records, and the dashboard reads Application records back out. Keeping
this schema in one place means the scrapers, the tailoring engine, the
apply engine, and the dashboard all agree on what a "job" and an
"application" look like.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class JobSource(str, Enum):
    LINKEDIN = "linkedin"
    INDEED = "indeed"
    SIMPLYHIRED = "simplyhired"
    # A job added by hand via the dashboard's "Add Job by URL" flow (see
    # POST /api/jobs in src/dashboard/app.py) rather than found by one of
    # the real scrapers above - kept distinct so the Applications page's
    # source filter/badge can tell the two apart honestly.
    MANUAL = "manual"


class ApplicationStatus(str, Enum):
    PENDING = "pending"  # found + tailored, not yet attempted
    APPLIED = "applied"
    FAILED = "failed"
    BLOCKED = "blocked"  # circuit breaker tripped (CAPTCHA/block signal)
    SKIPPED_DUPLICATE = "skipped_duplicate"


class Job(BaseModel):
    """A job posting found by a scraper."""

    job_id: str
    source: JobSource
    title: str
    company: str
    location: Optional[str] = None
    url: str
    description: Optional[str] = None
    posted_at: Optional[datetime] = None
    first_seen_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class Application(BaseModel):
    """One attempt (or planned attempt) to apply to a Job. This is what
    the admin dashboard reads and displays."""

    job_id: str
    source: JobSource
    title: str
    company: str
    url: str
    status: ApplicationStatus
    reason: Optional[str] = None  # why it failed/was blocked/was skipped
    resume_s3_key: Optional[str] = None
    # Basename of the tailored PDF as written by
    # src/tailoring/engine.py:_resume_filename (job-title-timestamp, e.g.
    # "senior-backend-engineer-20260910153045.pdf") - NOT the same string
    # as resume_s3_key's full "resumes/<filename>" key. Set on both
    # backends (local disk always writes under this name; S3's key is
    # just "resumes/" + this same filename) so the dashboard's download
    # route and "My Resumes" list can find/label the right file without
    # having to reconstruct a name from job_id.
    resume_filename: Optional[str] = None
    applied_at: Optional[datetime] = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Populated by the ingest pipeline (src/pipeline/ingest.py) at scrape
    # time - see src/common/skills.py for how required_skills is derived.
    required_skills: list[str] = Field(default_factory=list)
    posted_at: Optional[datetime] = None  # when the job was posted, if the source exposed it
    is_remote: bool = False
    category: Optional[str] = None  # which config/search_criteria.yaml entry found this job

    # Set by an on-demand "Tailor Resume" click (src/dashboard/app.py's
    # /api/applications/{job_id}/tailor) rather than the initial scrape -
    # lets the dashboard show "PDF updated <when>" for a job the operator
    # re-tailored themselves. required_skills above is left untouched by
    # this (it stays "what the scraper found at scrape time"); this field
    # is "what the last tailoring pass actually used", which may be a
    # superset if a fresh fetch of the posting turned up more.
    resume_tailored_at: Optional[datetime] = None
    resume_tailored_skills: list[str] = Field(default_factory=list)
