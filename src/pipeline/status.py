"""Tiny status/log file so the dashboard's Settings page (local runs
only — see LOCAL_ACTIONS_ENABLED in src/dashboard/app.py) can show live
progress while src/pipeline/ingest.py runs in a background thread.

Local scratch only — both files live under data/, which is git-ignored.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

STATUS_PATH = Path("data/ingest_status.json")
LOG_PATH = Path("data/ingest_log.txt")
MAX_LOG_LINES = 200


def write_status(**fields) -> None:
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    current = read_status()
    current.update(fields)
    current["updated_at"] = datetime.now(timezone.utc).isoformat()
    STATUS_PATH.write_text(json.dumps(current, indent=2), encoding="utf-8")


def read_status() -> dict:
    if not STATUS_PATH.exists():
        return {"running": False}
    return json.loads(STATUS_PATH.read_text(encoding="utf-8"))


def append_log(line: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing = LOG_PATH.read_text(encoding="utf-8").splitlines() if LOG_PATH.exists() else []
    timestamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
    existing.append(f"[{timestamp}] {line}")
    existing = existing[-MAX_LOG_LINES:]
    LOG_PATH.write_text("\n".join(existing) + "\n", encoding="utf-8")


def read_log(tail: int = 50) -> list[str]:
    if not LOG_PATH.exists():
        return []
    lines = LOG_PATH.read_text(encoding="utf-8").splitlines()
    return lines[-tail:]
