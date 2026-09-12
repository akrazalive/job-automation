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

# A genuinely running scrape calls write_status() at least once per
# search (plus append_log() per progress line) - real gaps between those
# writes are at most a few dozen seconds (human_delay's 5-12s inter-
# search pause plus one search's own page loads), never minutes. Direct
# feedback 2026-09-12: an orphaned run - one whose process died mid-scrape
# (e.g. `uvicorn --reload` restarting after a code edit, or the terminal
# it was started from being closed) without ever reaching the code that
# writes running=False - left this file stuck at running=true forever,
# with nothing left alive to ever flip it back. Two knock-on symptoms:
# the dashboard's "Scrape now" button stayed disabled permanently (it
# just mirrors this file's `running`), and "Stop" appeared to do nothing
# (request_stop() only sets a flag; should_stop() needs an actual live
# run() loop polling it to have any effect at all). read_status() now
# treats a `running: true` status this stale as dead rather than
# trusting it forever, which un-sticks both symptoms without needing a
# real live process to ever clean up after itself.
STALE_RUNNING_SECONDS = 300


def write_status(**fields) -> None:
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    current = read_status()
    current.update(fields)
    current["updated_at"] = datetime.now(timezone.utc).isoformat()
    STATUS_PATH.write_text(json.dumps(current, indent=2), encoding="utf-8")


def read_status() -> dict:
    """Returns the raw status file contents, EXCEPT `running` is forced
    False when the file claims running=True but hasn't been touched in
    STALE_RUNNING_SECONDS — see that constant's comment. Deliberately a
    pure read (never writes the correction back to disk): the next real
    run() call overwrites the whole file anyway via its own
    write_status(running=True, ...) at start, and not writing here avoids
    any risk of this "healing" read racing a genuinely-just-started run
    whose first write hasn't landed yet (impossible in practice at a
    5-minute threshold, but there's no reason to risk it for a write this
    function doesn't need to make)."""
    if not STATUS_PATH.exists():
        return {"running": False}
    status = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
    if status.get("running") and _is_stale(status):
        status = {**status, "running": False, "stale_recovered": True}
    return status


def _is_stale(status: dict) -> bool:
    updated_at = status.get("updated_at")
    if not updated_at:
        return True  # no timestamp to trust at all - assume dead, not eternally alive
    try:
        last_update = datetime.fromisoformat(updated_at)
    except ValueError:
        return True
    return (datetime.now(timezone.utc) - last_update).total_seconds() > STALE_RUNNING_SECONDS


def request_stop() -> None:
    """Called by the dashboard's "Stop" button (POST /actions/scrape-stop)
    — records that the currently-running scrape should halt at its next
    safe checkpoint. Written to the same status file the background
    thread already polls into, rather than an in-process flag, so it
    works the same way every other cross-thread signal here does (the
    dashboard route and the ingest run live in different threads within
    one process, but sharing state via this file — not a shared Python
    object — is what already made the "is a scrape running" check in
    trigger_scrape() correct across a dashboard reload)."""
    write_status(stop_requested=True)


def should_stop() -> bool:
    """Polled by src.pipeline.ingest.run() between searches (and it's
    cheap - one small JSON file read - so polling every search, not just
    once, is fine)."""
    return bool(read_status().get("stop_requested"))


def clear_stop() -> None:
    """Called at the start of every run() so a stop requested during a
    PREVIOUS scrape can never leak into halting the next one before it
    even gets going."""
    write_status(stop_requested=False)


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
