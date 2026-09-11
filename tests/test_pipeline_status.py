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
