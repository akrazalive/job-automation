"""Local, file-backed ApplicationStore — no AWS required.

This lets the dashboard be built and tested before infra/aws is deployed,
and lets the local ingest pipeline (src/pipeline/ingest.py) work without
AWS credentials at all. It is NOT meant for production use (no
concurrency safety, no real querying) — once infra/aws/template.yaml is
deployed, switch to it with STORAGE_BACKEND=aws (see dynamo_store.py).

The backing file lives under data/, which is git-ignored — it's scratch/
sample data, not a source of truth.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from src.common.job_schema import Application, ApplicationStatus
from src.storage.base import ApplicationStore, filter_records

DEFAULT_DATA_FILE = Path("data/local_applications.json")


def _seed_data() -> list[dict]:
    """Illustrative sample records so the dashboard has something to show
    on a totally fresh clone, before the ingest pipeline has ever run.
    Written only when the data file doesn't exist yet — as soon as
    src/pipeline/ingest.py runs once, these are replaced by real scraped
    jobs. Job URLs here are deliberately NOT real postings.
    """
    now = datetime.now(timezone.utc)
    samples = [
        dict(
            job_id="sample-li-1001", source="linkedin", title="Senior Full Stack Developer",
            company="Acme Corp (sample data)", url="https://linkedin.com/jobs/view/1001",
            status="applied", reason=None, resume_s3_key=None,
            applied_at=(now - timedelta(hours=3)).isoformat(),
            required_skills=["React", "Node.js", "TypeScript"], is_remote=True, category="fullstack",
        ),
        dict(
            job_id="sample-in-2002", source="indeed", title="Full Stack Engineer (React/Node)",
            company="Globex Inc (sample data)", url="https://indeed.com/viewjob?jk=2002",
            status="applied", reason=None, resume_s3_key=None,
            applied_at=(now - timedelta(hours=5)).isoformat(),
            required_skills=["React", "Node.js"], is_remote=True, category="fullstack",
        ),
        dict(
            job_id="sample-li-1002", source="linkedin", title="Frontend Developer",
            company="Initech (sample data)", url="https://linkedin.com/jobs/view/1002",
            status="failed",
            reason="Unhandled screening question: 'Describe a time you led a project'",
            resume_s3_key=None, applied_at=None,
            required_skills=["React", "Vue.js"], is_remote=False, category="frontend",
        ),
        dict(
            job_id="sample-sh-3001", source="simplyhired", title="Backend Developer (Python)",
            company="Umbrella LLC (sample data)", url="https://simplyhired.com/job/3001",
            status="failed", reason="External ATS redirect - manual apply required",
            resume_s3_key=None, applied_at=None,
            required_skills=["Python", "Django", "PostgreSQL"], is_remote=True, category="backend",
        ),
        dict(
            job_id="sample-li-1003", source="linkedin", title="Full Stack Developer",
            company="Wayne Enterprises (sample data)", url="https://linkedin.com/jobs/view/1003",
            status="blocked", reason="CAPTCHA challenge detected mid-apply",
            resume_s3_key=None, applied_at=None,
            required_skills=["PHP", "Laravel", "MySQL"], is_remote=False, category="php",
        ),
        dict(
            job_id="sample-in-2003", source="indeed", title="Software Engineer, Full Stack",
            company="Acme Corp (sample data)", url="https://indeed.com/viewjob?jk=2003",
            status="skipped_duplicate", reason="Already applied via LinkedIn (sample-li-1001)",
            resume_s3_key=None, applied_at=None,
            required_skills=["React", "Node.js"], is_remote=True, category="fullstack",
        ),
        dict(
            job_id="sample-li-1004", source="linkedin", title="Full Stack Developer (Python/React)",
            company="Stark Industries (sample data)", url="https://linkedin.com/jobs/view/1004",
            status="pending", reason=None, resume_s3_key=None, applied_at=None,
            required_skills=["Python", "React"], is_remote=True, category="fullstack",
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
        category: Optional[str] = None,
        title: Optional[str] = None,
        search: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> tuple[list[Application], Optional[str]]:
        records = filter_records(
            self._read(), status=status, source=source, company=company, category=category,
            title=title, search=search, date_from=date_from, date_to=date_to,
        )
        records.sort(key=lambda r: r["updated_at"], reverse=True)

        # Cursor is the offset into this filtered+sorted list, as a
        # decimal string — DynamoStore uses the identical scheme (see its
        # own list_applications), so this is a real cross-backend
        # contract now, not just an implementation detail — see
        # ApplicationStore.list_applications's CURSOR CONTRACT note.
        offset = int(cursor) if cursor else 0
        page = records[offset : offset + limit]
        next_cursor = str(offset + limit) if offset + limit < len(records) else None
        return [Application(**r) for r in page], next_cursor

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
        return len(filter_records(
            self._read(), status=status, source=source, company=company, category=category,
            title=title, search=search, date_from=date_from, date_to=date_to,
        ))

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
        return None  # no S3 in local dev — see src/dashboard/app.py's
        # /api/applications/{job_id}/resume, which falls back to serving
        # resume/output/<application.resume_filename> directly from disk
        # in this backend (or resume/output/<job_id>.pdf for an
        # application tailored before that field existed).

    def save_application(self, application: Application) -> None:
        records = self._read()
        data = json.loads(application.model_dump_json())
        records = [r for r in records if r["job_id"] != application.job_id]
        records.append(data)
        self._write(records)

    def delete_application(self, job_id: str) -> bool:
        records = self._read()
        remaining = [r for r in records if r["job_id"] != job_id]
        if len(remaining) == len(records):
            return False
        self._write(remaining)
        return True

    def get_category_breakdown(self) -> dict:
        records = self._read()
        breakdown: dict[str, dict[str, int]] = {}
        for r in records:
            cat = r.get("category") or "Uncategorized"
            bucket = breakdown.setdefault(cat, {"total": 0})
            bucket["total"] += 1
            bucket[r["status"]] = bucket.get(r["status"], 0) + 1
        return breakdown

    def mark_applied(self, job_id: str) -> bool:
        records = self._read()
        now = datetime.now(timezone.utc).isoformat()
        found = False
        for r in records:
            if r["job_id"] == job_id:
                r["status"] = "applied"
                r["applied_at"] = now
                r["updated_at"] = now
                found = True
                break
        if found:
            self._write(records)
        return found

    def get_application(self, job_id: str) -> Optional[Application]:
        for r in self._read():
            if r["job_id"] == job_id:
                return Application(**r)
        return None

    def update_resume_tailoring(
        self,
        job_id: str,
        resume_s3_key: Optional[str],
        tailored_skills: list[str],
        resume_filename: Optional[str] = None,
    ) -> Optional[str]:
        records = self._read()
        now = datetime.now(timezone.utc).isoformat()
        found = False
        for r in records:
            if r["job_id"] == job_id:
                r["resume_tailored_at"] = now
                r["resume_tailored_skills"] = tailored_skills
                r["updated_at"] = now
                if resume_s3_key:
                    r["resume_s3_key"] = resume_s3_key
                if resume_filename:
                    r["resume_filename"] = resume_filename
                found = True
                break
        if found:
            self._write(records)
        return now if found else None
