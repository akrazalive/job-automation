"""Local, file-backed ApplicationStore — no AWS required.

This lets the dashboard be built and tested before infra/aws is deployed.
It is NOT meant for production use (no concurrency safety, no real
querying) — once infra/aws/template.yaml is deployed, switch to it with
STORAGE_BACKEND=aws (see src/storage/dynamo_store.py).

The backing file lives under data/, which is git-ignored — it's scratch/
sample data, not a source of truth.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from src.common.job_schema import Application, ApplicationStatus
from src.storage.base import ApplicationStore

DEFAULT_DATA_FILE = Path("data/local_applications.json")


def _seed_data() -> list[dict]:
    """Sample records so the dashboard has something to show before any
    real scraping/applying has happened. This file is only ever written
    once (when it doesn't exist yet) — real runs will overwrite it with
    real application records once the apply engine (Phase 4/5) exists.
    """
    now = datetime.now(timezone.utc)
    samples = [
        dict(
            job_id="li-1001", source="linkedin", title="Senior Full Stack Developer",
            company="Acme Corp", url="https://linkedin.com/jobs/view/1001",
            status="applied", reason=None, resume_s3_key=None,
            applied_at=(now - timedelta(hours=3)).isoformat(),
        ),
        dict(
            job_id="in-2002", source="indeed", title="Full Stack Engineer (React/Node)",
            company="Globex Inc", url="https://indeed.com/viewjob?jk=2002",
            status="applied", reason=None, resume_s3_key=None,
            applied_at=(now - timedelta(hours=5)).isoformat(),
        ),
        dict(
            job_id="li-1002", source="linkedin", title="Frontend Developer",
            company="Initech", url="https://linkedin.com/jobs/view/1002",
            status="failed",
            reason="Unhandled screening question: 'Describe a time you led a project'",
            resume_s3_key=None, applied_at=None,
        ),
        dict(
            job_id="sh-3001", source="simplyhired", title="Backend Developer (Python)",
            company="Umbrella LLC", url="https://simplyhired.com/job/3001",
            status="failed", reason="External ATS redirect - manual apply required",
            resume_s3_key=None, applied_at=None,
        ),
        dict(
            job_id="li-1003", source="linkedin", title="Full Stack Developer",
            company="Wayne Enterprises", url="https://linkedin.com/jobs/view/1003",
            status="blocked", reason="CAPTCHA challenge detected mid-apply",
            resume_s3_key=None, applied_at=None,
        ),
        dict(
            job_id="in-2003", source="indeed", title="Software Engineer, Full Stack",
            company="Acme Corp", url="https://indeed.com/viewjob?jk=2003",
            status="skipped_duplicate", reason="Already applied via LinkedIn (li-1001)",
            resume_s3_key=None, applied_at=None,
        ),
        dict(
            job_id="li-1004", source="linkedin", title="Full Stack Developer (Python/React)",
            company="Stark Industries", url="https://linkedin.com/jobs/view/1004",
            status="pending", reason=None, resume_s3_key=None, applied_at=None,
        ),
    ]
    for i, s in enumerate(samples):
        s["updated_at"] = (now - timedelta(hours=i)).isoformat()
    return samples


class LocalJsonStore(ApplicationStore):
    def __init__(self, data_file: Optional[Path] = None):
        self.data_file = data_file or DEFAULT_DATA_FILE
        self.data_file.parent.mkdir(parents=True, exist_ok=True)
        if not self.data_file.exists():
            self._write(_seed_data())

    def _read(self) -> list[dict]:
        return json.loads(self.data_file.read_text(encoding="utf-8"))

    def _write(self, records: list[dict]) -> None:
        self.data_file.write_text(json.dumps(records, indent=2), encoding="utf-8")

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
        records = self._read()
        if status:
            records = [r for r in records if r["status"] == status]
        if source:
            records = [r for r in records if r["source"] == source]
        if company:
            records = [r for r in records if company.lower() in r["company"].lower()]
        if date_from:
            records = [r for r in records if (r.get("applied_at") or r["updated_at"]) >= date_from]
        if date_to:
            records = [r for r in records if (r.get("applied_at") or r["updated_at"]) <= date_to]
        records.sort(key=lambda r: r["updated_at"], reverse=True)
        page = records[:limit]
        return [Application(**r) for r in page], None

    def get_summary(self) -> dict:
        records = self._read()
        summary = {s.value: 0 for s in ApplicationStatus}
        for r in records:
            summary[r["status"]] = summary.get(r["status"], 0) + 1
        summary["total"] = len(records)
        return summary

    def get_job(self, job_id: str):
        return None  # local dev store doesn't keep a separate Jobs table

    def get_resume_url(self, job_id: str) -> Optional[str]:
        return None  # no S3 in local dev
