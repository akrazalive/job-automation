from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.common.job_schema import Application, ApplicationStatus, JobSource
from src.storage.local_store import LocalJsonStore


def _make_application(
    job_id: str, category: str = "fullstack", status: str = "pending", updated_at=None
) -> Application:
    return Application(
        job_id=job_id,
        source=JobSource.SIMPLYHIRED,
        title="Full Stack Developer",
        company="Test Co",
        url=f"https://example.com/{job_id}",
        status=ApplicationStatus(status),
        required_skills=["React", "Node.js"],
        is_remote=True,
        category=category,
        updated_at=updated_at or datetime.now(timezone.utc),
    )


def test_save_application_then_list(tmp_path: Path):
    store = LocalJsonStore(data_file=tmp_path / "apps.json")
    store.save_application(_make_application("job-1"))
    apps, _ = store.list_applications(limit=1000)
    assert any(a.job_id == "job-1" for a in apps)


def test_save_application_upserts_by_job_id(tmp_path: Path):
    store = LocalJsonStore(data_file=tmp_path / "apps.json")
    store.save_application(_make_application("job-1", status="pending"))
    store.save_application(_make_application("job-1", status="applied"))
    apps, _ = store.list_applications(limit=1000)
    matching = [a for a in apps if a.job_id == "job-1"]
    assert len(matching) == 1
    assert matching[0].status.value == "applied"


def test_category_breakdown_counts(tmp_path: Path):
    data_file = tmp_path / "apps.json"
    data_file.write_text("[]", encoding="utf-8")  # avoid seed-data pollution of the counts below
    store = LocalJsonStore(data_file=data_file)
    store.save_application(_make_application("job-1", category="frontend", status="applied"))
    store.save_application(_make_application("job-2", category="frontend", status="pending"))
    store.save_application(_make_application("job-3", category="backend", status="pending"))
    breakdown = store.get_category_breakdown()
    assert breakdown["frontend"]["total"] == 2
    assert breakdown["frontend"]["applied"] == 1
    assert breakdown["backend"]["total"] == 1


def test_saved_application_preserves_required_skills_and_remote(tmp_path: Path):
    store = LocalJsonStore(data_file=tmp_path / "apps.json")
    store.save_application(_make_application("job-1"))
    apps, _ = store.list_applications(limit=1000)
    saved = next(a for a in apps if a.job_id == "job-1")
    assert saved.required_skills == ["React", "Node.js"]
    assert saved.is_remote is True


def test_pagination_returns_next_cursor_when_more_remain(tmp_path: Path):
    data_file = tmp_path / "apps.json"
    data_file.write_text("[]", encoding="utf-8")
    store = LocalJsonStore(data_file=data_file)
    base = datetime.now(timezone.utc)
    for i in range(5):
        store.save_application(_make_application(f"job-{i}", updated_at=base + timedelta(minutes=i)))

    page1, cursor1 = store.list_applications(limit=2)
    assert len(page1) == 2
    assert cursor1 == "2"

    page2, cursor2 = store.list_applications(limit=2, cursor=cursor1)
    assert len(page2) == 2
    assert cursor2 == "4"

    page3, cursor3 = store.list_applications(limit=2, cursor=cursor2)
    assert len(page3) == 1
    assert cursor3 is None

    # No overlap between pages, and together they cover everything.
    seen_ids = {a.job_id for a in page1 + page2 + page3}
    assert seen_ids == {f"job-{i}" for i in range(5)}


def test_pagination_no_cursor_when_everything_fits_on_one_page(tmp_path: Path):
    data_file = tmp_path / "apps.json"
    data_file.write_text("[]", encoding="utf-8")
    store = LocalJsonStore(data_file=data_file)
    store.save_application(_make_application("job-1"))
    apps, cursor = store.list_applications(limit=100)
    assert len(apps) == 1
    assert cursor is None
