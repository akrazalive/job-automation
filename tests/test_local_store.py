from pathlib import Path

from src.storage.local_store import LocalJsonStore


def test_seed_and_list(tmp_path: Path):
    store = LocalJsonStore(data_file=tmp_path / "apps.json")
    apps, cursor = store.list_applications(limit=100)
    assert len(apps) > 0
    assert cursor is None


def test_filter_by_status(tmp_path: Path):
    store = LocalJsonStore(data_file=tmp_path / "apps.json")
    apps, _ = store.list_applications(status="applied")
    assert apps
    assert all(a.status.value == "applied" for a in apps)


def test_filter_by_company_is_case_insensitive(tmp_path: Path):
    store = LocalJsonStore(data_file=tmp_path / "apps.json")
    apps, _ = store.list_applications(company="acme")
    assert apps
    assert all("acme" in a.company.lower() for a in apps)


def test_summary_counts_match_total(tmp_path: Path):
    store = LocalJsonStore(data_file=tmp_path / "apps.json")
    summary = store.get_summary()
    apps, _ = store.list_applications(limit=1000)
    assert summary["total"] == len(apps)
    assert sum(v for k, v in summary.items() if k != "total") == summary["total"]
