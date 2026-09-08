"""Duplicate-detection for job postings.

A job is "the same" if company+title+url hash to the same key, regardless
of which search (or which run) found it. This is a local JSON stopgap —
once DynamoDB (Phase 3) is wired up, `job_id_for()` becomes the DynamoDB
Jobs table's partition key and a get_item lookup replaces SeenJobsCache
entirely (see storage/dynamo_store.py, which already assumes job_id is
the primary key).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Optional

DEFAULT_SEEN_FILE = Path("data/seen_jobs.json")


def job_id_for(source: str, company: str, title: str, url: str) -> str:
    """Deterministic id for a job posting, prefixed with the source so
    ids stay readable/debuggable (e.g. "simplyhired-3f9a1c2b4d5e6f7a")."""
    key = f"{company.strip().lower()}|{title.strip().lower()}|{url.strip().lower()}"
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    return f"{source}-{digest}"


class SeenJobsCache:
    """Local JSON dedupe store. Not for production once DynamoDB exists —
    swap callers to a DynamoDB get_item check on job_id instead."""

    def __init__(self, path: Optional[Path] = None):
        self.path = path or DEFAULT_SEEN_FILE
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            self._seen: set[str] = set(json.loads(self.path.read_text(encoding="utf-8")))
        else:
            self._seen = set()

    def is_seen(self, job_id: str) -> bool:
        return job_id in self._seen

    def mark_seen(self, job_id: str) -> None:
        self._seen.add(job_id)
        self.path.write_text(json.dumps(sorted(self._seen), indent=2), encoding="utf-8")
