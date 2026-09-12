from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.pipeline import status as pipeline_status


def _isolate(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(pipeline_status, "STATUS_PATH", tmp_path / "status.json")
    monkeypatch.setattr(pipeline_status, "LOG_PATH", tmp_path / "log.txt")


def test_should_stop_is_false_by_default(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    assert pipeline_status.should_stop() is False


def test_request_stop_then_should_stop(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    pipeline_status.request_stop()
    assert pipeline_status.should_stop() is True


def test_clear_stop_resets_the_flag(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    pipeline_status.request_stop()
    pipeline_status.clear_stop()
    assert pipeline_status.should_stop() is False


def test_request_stop_preserves_other_status_fields(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    pipeline_status.write_status(running=True, total_found=5)
    pipeline_status.request_stop()
    status = pipeline_status.read_status()
    assert status["running"] is True
    assert status["total_found"] == 5
    assert status["stop_requested"] is True


# --- Stale "running" recovery ----------------------------------------------
# Regression coverage for a real bug: a scrape whose process died mid-run
# (e.g. `uvicorn --reload` restarting after a code edit) left this file
# stuck at running=true forever, with nothing left alive to ever flip it
# back — the dashboard's "Scrape now" button stayed permanently disabled
# and "Stop" had no live run() loop left to signal. See
# STALE_RUNNING_SECONDS's own comment in src/pipeline/status.py.

def _write_raw_status(path: Path, **fields) -> None:
    """Writes the status file directly (bypassing write_status(), which
    always stamps its OWN current updated_at) so these tests can control
    exactly how old updated_at is."""
    import json

    path.write_text(json.dumps(fields, indent=2), encoding="utf-8")


def test_read_status_treats_a_recently_updated_running_status_as_alive(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    pipeline_status.write_status(running=True, total_found=1)
    status = pipeline_status.read_status()
    assert status["running"] is True
    assert "stale_recovered" not in status


def test_read_status_recovers_a_stale_running_status(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    old = datetime.now(timezone.utc) - timedelta(seconds=pipeline_status.STALE_RUNNING_SECONDS + 60)
    _write_raw_status(pipeline_status.STATUS_PATH, running=True, total_found=87, updated_at=old.isoformat())

    status = pipeline_status.read_status()

    assert status["running"] is False
    assert status["stale_recovered"] is True
    assert status["total_found"] == 87  # every other field is preserved, not wiped


def test_read_status_treats_a_running_status_with_no_timestamp_as_stale(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    _write_raw_status(pipeline_status.STATUS_PATH, running=True)

    status = pipeline_status.read_status()

    assert status["running"] is False
    assert status["stale_recovered"] is True


def test_should_stop_is_unaffected_by_staleness_recovery(monkeypatch, tmp_path):
    # should_stop() only ever cares about stop_requested - staleness
    # recovery must never mask or alter that flag.
    _isolate(monkeypatch, tmp_path)
    old = datetime.now(timezone.utc) - timedelta(seconds=pipeline_status.STALE_RUNNING_SECONDS + 60)
    _write_raw_status(
        pipeline_status.STATUS_PATH, running=True, stop_requested=True, updated_at=old.isoformat(),
    )
    assert pipeline_status.should_stop() is True


def test_read_status_does_not_persist_the_stale_correction_to_disk(monkeypatch, tmp_path):
    # Deliberately a pure read (see read_status()'s own docstring) - the
    # on-disk file is left untouched; only what's RETURNED is corrected.
    _isolate(monkeypatch, tmp_path)
    old = datetime.now(timezone.utc) - timedelta(seconds=pipeline_status.STALE_RUNNING_SECONDS + 60)
    _write_raw_status(pipeline_status.STATUS_PATH, running=True, updated_at=old.isoformat())

    pipeline_status.read_status()

    import json
    raw = json.loads(pipeline_status.STATUS_PATH.read_text(encoding="utf-8"))
    assert raw["running"] is True  # untouched on disk
