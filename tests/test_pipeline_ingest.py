from datetime import datetime, timezone
from pathlib import Path

from src.common.job_schema import Job, JobSource
from src.pipeline import ingest
from src.storage.local_store import LocalJsonStore


def _job(source: str, key: str) -> Job:
    return Job(
        job_id=f"{source}-{key}",
        source=JobSource(source),
        title=f"{source.title()} Developer",
        company=f"{key} Co",
        location="Remote",
        url=f"https://example.com/{source}/{key}",
        description="React and Node.js role, fully remote, no restrictions.",
        posted_at=datetime.now(timezone.utc),
    )


def _patch_common(monkeypatch, tmp_path: Path, config_yaml: str) -> LocalJsonStore:
    """Shared plumbing for every ingest test below: a real (but tmp)
    store so save_application()'s actual behavior is exercised, and every
    disk-touching/slow side effect (config file, seen-jobs cache, status/
    log files, inter-search delay) redirected to tmp_path or no-op'd so
    the test is fast and never touches the real repo's data/ files."""
    config_path = tmp_path / "search_criteria.yaml"
    config_path.write_text(config_yaml, encoding="utf-8")
    monkeypatch.setattr(ingest, "CONFIG_PATH", config_path)

    store = LocalJsonStore(data_file=tmp_path / "apps.json")
    store._write([])  # drop LocalJsonStore's illustrative seed data — irrelevant noise here
    monkeypatch.setattr(ingest, "get_store", lambda: store)

    def fake_seen_cache_init(self, path=None):
        self._seen = set()
        self.path = tmp_path / "seen.json"

    monkeypatch.setattr(ingest.SeenJobsCache, "__init__", fake_seen_cache_init)
    monkeypatch.setattr(ingest, "human_delay", lambda *a, **kw: None)
    monkeypatch.setattr(ingest.pipeline_status, "write_status", lambda **kw: None)
    monkeypatch.setattr(ingest.pipeline_status, "append_log", lambda line: None)
    # should_stop/clear_stop touch the real data/ingest_status.json by
    # default (see src/pipeline/status.py) — isolate these tests from
    # whatever that real file happens to contain on the machine running
    # the suite, same reasoning as every other patch in this function.
    monkeypatch.setattr(ingest.pipeline_status, "should_stop", lambda: False)
    monkeypatch.setattr(ingest.pipeline_status, "clear_stop", lambda: None)
    return store


def test_run_calls_every_configured_source_and_saves_no_resume(monkeypatch, tmp_path):
    config_yaml = """
sources:
  - simplyhired
  - indeed
searches:
  - query: "React Developer"
    location: "Remote"
    category: "frontend"
"""
    store = _patch_common(monkeypatch, tmp_path, config_yaml)

    calls = []

    def fake_simplyhired(query, location, max_results=8, **kw):
        calls.append("simplyhired")
        return [_job("simplyhired", "1")]

    def fake_indeed(query, location, max_results=8, **kw):
        calls.append("indeed")
        return [_job("indeed", "1")]

    monkeypatch.setitem(ingest.SOURCE_SCRAPERS, "simplyhired", fake_simplyhired)
    monkeypatch.setitem(ingest.SOURCE_SCRAPERS, "indeed", fake_indeed)

    result = ingest.run()

    assert sorted(calls) == ["indeed", "simplyhired"]
    assert result["total_found"] == 2
    assert result["total_new"] == 2

    apps, _ = store.list_applications(limit=10)
    assert len(apps) == 2
    for app in apps:
        # The core behavior change this pass: no tailoring happens during
        # a scrape, on purpose (see ingest.py's module docstring) — every
        # freshly scraped job must land with no resume attached yet.
        assert app.resume_s3_key is None
        assert app.resume_filename is None
        assert app.resume_tailored_at is None
        assert "React" in app.required_skills or "Node.js" in app.required_skills


def test_run_defaults_to_simplyhired_when_sources_key_missing(monkeypatch, tmp_path):
    config_yaml = """
searches:
  - query: "Web Developer"
    location: "Remote"
    category: "general"
"""
    _patch_common(monkeypatch, tmp_path, config_yaml)

    calls = []
    monkeypatch.setitem(ingest.SOURCE_SCRAPERS, "simplyhired", lambda *a, **kw: calls.append("simplyhired") or [])
    monkeypatch.setitem(ingest.SOURCE_SCRAPERS, "indeed", lambda *a, **kw: calls.append("indeed") or [])
    monkeypatch.setitem(ingest.SOURCE_SCRAPERS, "linkedin", lambda *a, **kw: calls.append("linkedin") or [])

    ingest.run()

    assert calls == ["simplyhired"]


def test_run_one_source_failing_does_not_abort_the_others(monkeypatch, tmp_path):
    config_yaml = """
sources:
  - simplyhired
  - indeed
searches:
  - query: "Laravel Developer"
    location: "Remote"
    category: "php"
"""
    store = _patch_common(monkeypatch, tmp_path, config_yaml)

    def broken_simplyhired(*a, **kw):
        raise RuntimeError("boom")

    def fake_indeed(*a, **kw):
        return [_job("indeed", "ok")]

    monkeypatch.setitem(ingest.SOURCE_SCRAPERS, "simplyhired", broken_simplyhired)
    monkeypatch.setitem(ingest.SOURCE_SCRAPERS, "indeed", fake_indeed)

    result = ingest.run()

    assert result["total_new"] == 1
    apps, _ = store.list_applications(limit=10)
    assert len(apps) == 1
    assert apps[0].source == JobSource.INDEED


def test_run_rejects_unknown_source_in_config(monkeypatch, tmp_path):
    config_yaml = """
sources:
  - glassdoor
searches:
  - query: "Web Developer"
    location: "Remote"
"""
    _patch_common(monkeypatch, tmp_path, config_yaml)

    try:
        ingest.run()
        assert False, "expected a ValueError for an unknown source"
    except ValueError as exc:
        assert "glassdoor" in str(exc)


def _onsite_job(source: str, key: str, location: str) -> Job:
    return Job(
        job_id=f"{source}-{key}",
        source=JobSource(source),
        title=f"{source.title()} Developer",
        company=f"{key} Co",
        location=location,
        url=f"https://example.com/{source}/{key}",
        description="Onsite role, no remote work available.",
        posted_at=datetime.now(timezone.utc),
    )


def test_indeed_keeps_onsite_job_in_an_allowed_country(monkeypatch, tmp_path):
    config_yaml = """
sources:
  - indeed
searches:
  - query: "React Developer"
    location: "Remote"
    category: "frontend"
"""
    store = _patch_common(monkeypatch, tmp_path, config_yaml)
    monkeypatch.setitem(
        ingest.SOURCE_SCRAPERS, "indeed",
        lambda *a, **kw: [_onsite_job("indeed", "1", "Karachi, Pakistan")],
    )

    ingest.run()

    apps, _ = store.list_applications(limit=10)
    assert len(apps) == 1
    assert apps[0].is_remote is False
    assert apps[0].location == "Karachi, Pakistan"


def test_indeed_drops_onsite_job_outside_allowed_countries(monkeypatch, tmp_path):
    config_yaml = """
sources:
  - indeed
searches:
  - query: "React Developer"
    location: "Remote"
    category: "frontend"
"""
    store = _patch_common(monkeypatch, tmp_path, config_yaml)
    monkeypatch.setitem(
        ingest.SOURCE_SCRAPERS, "indeed",
        lambda *a, **kw: [_onsite_job("indeed", "1", "Berlin, Germany")],
    )

    result = ingest.run()

    assert result["total_new"] == 0
    assert result["total_skipped"] == 1
    apps, _ = store.list_applications(limit=10)
    assert len(apps) == 0


def test_simplyhired_drops_onsite_job_even_in_an_allowed_country(monkeypatch, tmp_path):
    # SimplyHired stays remote-only — the ALLOWED_ONSITE_COUNTRIES
    # allowance is deliberately Indeed/LinkedIn only, see
    # src/pipeline/ingest.py.
    config_yaml = """
sources:
  - simplyhired
searches:
  - query: "React Developer"
    location: "Remote"
    category: "frontend"
"""
    store = _patch_common(monkeypatch, tmp_path, config_yaml)
    monkeypatch.setitem(
        ingest.SOURCE_SCRAPERS, "simplyhired",
        lambda *a, **kw: [_onsite_job("simplyhired", "1", "Karachi, Pakistan")],
    )

    result = ingest.run()

    assert result["total_new"] == 0
    apps, _ = store.list_applications(limit=10)
    assert len(apps) == 0


def test_only_simplyhired_search_is_scoped_by_the_entrys_location(monkeypatch, tmp_path):
    config_yaml = """
sources:
  - simplyhired
  - indeed
searches:
  - query: "React Developer"
    location: "Remote"
    category: "frontend"
"""
    _patch_common(monkeypatch, tmp_path, config_yaml)
    seen_locations = {}

    def fake_scraper(source):
        def _search(query, location, max_results=8, **kw):
            seen_locations[source] = location
            return []
        return _search

    monkeypatch.setitem(ingest.SOURCE_SCRAPERS, "simplyhired", fake_scraper("simplyhired"))
    monkeypatch.setitem(ingest.SOURCE_SCRAPERS, "indeed", fake_scraper("indeed"))

    ingest.run()

    assert seen_locations["simplyhired"] == "Remote"
    assert seen_locations["indeed"] == ""


def test_stop_request_halts_the_run_before_the_next_search(monkeypatch, tmp_path):
    config_yaml = """
sources:
  - simplyhired
searches:
  - query: "Web Developer"
    location: "Remote"
  - query: "React Developer"
    location: "Remote"
"""
    _patch_common(monkeypatch, tmp_path, config_yaml)
    calls = []

    # Simulate a Stop click landing after the first search completes -
    # should_stop() only starts returning True once one search has run.
    def fake_should_stop():
        return len(calls) >= 1

    def fake_scraper(*a, **kw):
        calls.append(1)
        return []

    monkeypatch.setattr(ingest.pipeline_status, "should_stop", fake_should_stop)
    monkeypatch.setitem(ingest.SOURCE_SCRAPERS, "simplyhired", fake_scraper)

    result = ingest.run()

    assert len(calls) == 1  # the second search never ran
    assert result["stopped_early"] is True
