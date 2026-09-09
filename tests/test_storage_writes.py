from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.common.job_schema import Application, ApplicationStatus, JobSource
from src.storage.local_store import LocalJsonStore


def _make_application(
    job_id: str, category: str = "fullstack", status: str = "pending", updated_at=None,
    title: str = "Full Stack Developer", company: str = "Test Co",
    required_skills: list[str] | None = None,
) -> Application:
    return Application(
        job_id=job_id,
        source=JobSource.SIMPLYHIRED,
        title=title,
        company=company,
        url=f"https://example.com/{job_id}",
        status=ApplicationStatus(status),
        required_skills=required_skills if required_skills is not None else ["React", "Node.js"],
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


def test_list_applications_filters_by_category(tmp_path: Path):
    data_file = tmp_path / "apps.json"
    data_file.write_text("[]", encoding="utf-8")
    store = LocalJsonStore(data_file=data_file)
    store.save_application(_make_application("job-1", category="frontend"))
    store.save_application(_make_application("job-2", category="backend"))

    apps, _ = store.list_applications(category="frontend", limit=1000)

    assert {a.job_id for a in apps} == {"job-1"}


def test_list_applications_filters_by_title_substring_case_insensitive(tmp_path: Path):
    data_file = tmp_path / "apps.json"
    data_file.write_text("[]", encoding="utf-8")
    store = LocalJsonStore(data_file=data_file)
    store.save_application(_make_application("job-1", title="Senior React Developer"))
    store.save_application(_make_application("job-2", title="Backend Engineer"))

    apps, _ = store.list_applications(title="react", limit=1000)

    assert {a.job_id for a in apps} == {"job-1"}


def test_list_applications_search_matches_title_company_or_skills(tmp_path: Path):
    data_file = tmp_path / "apps.json"
    data_file.write_text("[]", encoding="utf-8")
    store = LocalJsonStore(data_file=data_file)
    store.save_application(_make_application("job-1", title="Backend Engineer", company="Acme Corp"))
    store.save_application(_make_application("job-2", title="Frontend Dev", company="Globex", required_skills=["Vue.js"]))
    store.save_application(_make_application("job-3", title="Data Analyst", company="Initech", required_skills=["SQL"]))

    by_company, _ = store.list_applications(search="acme", limit=1000)
    assert {a.job_id for a in by_company} == {"job-1"}

    by_skill, _ = store.list_applications(search="vue", limit=1000)
    assert {a.job_id for a in by_skill} == {"job-2"}

    by_title, _ = store.list_applications(search="analyst", limit=1000)
    assert {a.job_id for a in by_title} == {"job-3"}


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


def test_mark_applied_updates_status_and_applied_at(tmp_path: Path):
    store = LocalJsonStore(data_file=tmp_path / "apps.json")
    store.save_application(_make_application("job-1", status="pending"))

    result = store.mark_applied("job-1")

    assert result is True
    apps, _ = store.list_applications(limit=1000)
    updated = next(a for a in apps if a.job_id == "job-1")
    assert updated.status.value == "applied"
    assert updated.applied_at is not None


def test_mark_applied_returns_false_for_unknown_job(tmp_path: Path):
    store = LocalJsonStore(data_file=tmp_path / "apps.json")
    assert store.mark_applied("does-not-exist") is False


def test_get_application_returns_matching_record(tmp_path: Path):
    store = LocalJsonStore(data_file=tmp_path / "apps.json")
    store.save_application(_make_application("job-1"))
    found = store.get_application("job-1")
    assert found is not None
    assert found.job_id == "job-1"


def test_get_application_returns_none_for_unknown_job(tmp_path: Path):
    store = LocalJsonStore(data_file=tmp_path / "apps.json")
    assert store.get_application("does-not-exist") is None


def test_update_resume_tailoring_sets_timestamp_and_skills(tmp_path: Path):
    store = LocalJsonStore(data_file=tmp_path / "apps.json")
    store.save_application(_make_application("job-1"))

    result = store.update_resume_tailoring("job-1", resume_s3_key=None, tailored_skills=["React", "AWS"])

    assert result is not None  # the ISO timestamp it set resume_tailored_at to
    updated = store.get_application("job-1")
    assert updated.resume_tailored_at is not None
    assert updated.resume_tailored_skills == ["React", "AWS"]


def test_update_resume_tailoring_sets_s3_key_when_given(tmp_path: Path):
    store = LocalJsonStore(data_file=tmp_path / "apps.json")
    store.save_application(_make_application("job-1"))

    store.update_resume_tailoring("job-1", resume_s3_key="resumes/job-1.pdf", tailored_skills=[])

    updated = store.get_application("job-1")
    assert updated.resume_s3_key == "resumes/job-1.pdf"


def test_update_resume_tailoring_returns_none_for_unknown_job(tmp_path: Path):
    store = LocalJsonStore(data_file=tmp_path / "apps.json")
    assert store.update_resume_tailoring("does-not-exist", None, []) is None
