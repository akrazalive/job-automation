from datetime import datetime, timezone
from pathlib import Path

from src.common.dedupe import job_id_for
from src.scrapers.simplyhired import (
    build_search_url,
    parse_date_stamp,
    parse_job_detail,
    parse_search_results,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_build_search_url():
    url = build_search_url("Full Stack Developer", "Remote")
    assert url.startswith("https://www.simplyhired.com/search?")
    assert "q=Full+Stack+Developer" in url
    assert "l=Remote" in url


def test_build_search_url_no_location():
    url = build_search_url("Full Stack Developer")
    assert "l=" not in url


def test_parse_search_results():
    html = (FIXTURES / "simplyhired_search.html").read_text(encoding="utf-8")
    rows = parse_search_results(html)
    # 3 cards in the fixture, but the 3rd (no href) must be skipped.
    assert len(rows) == 2
    assert rows[0].title == "Full Stack Developer"
    assert rows[0].company == "Acme Corp"
    assert rows[0].location == "Remote"
    assert rows[0].detail_url == "https://www.simplyhired.com/job/abc123"
    assert rows[0].date_text == "7d"
    assert rows[1].company == "Globex Inc"
    assert rows[1].date_text == "20h"


def test_parse_job_detail():
    html = (FIXTURES / "simplyhired_job_detail.html").read_text(encoding="utf-8")
    description = parse_job_detail(html)
    assert "Full Stack Developer with experience in React" in description
    assert "CI/CD pipelines" in description


def test_parse_job_detail_missing_section_returns_empty():
    assert parse_job_detail("<html><body>nothing here</body></html>") == ""


def test_job_id_is_deterministic_and_source_prefixed():
    a = job_id_for("simplyhired", "Acme Corp", "Full Stack Developer", "https://x/job/1")
    b = job_id_for("simplyhired", "Acme Corp", "Full Stack Developer", "https://x/job/1")
    c = job_id_for("simplyhired", "Acme Corp", "Full Stack Developer", "https://x/job/2")
    assert a == b
    assert a != c
    assert a.startswith("simplyhired-")


def test_parse_date_stamp_hours():
    now = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)
    result = parse_date_stamp("20h", now=now)
    assert result == datetime(2026, 9, 7, 16, 0, tzinfo=timezone.utc)


def test_parse_date_stamp_days():
    now = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)
    result = parse_date_stamp("7d", now=now)
    assert result == datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


def test_parse_date_stamp_case_insensitive_and_whitespace():
    now = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)
    assert parse_date_stamp(" 3D ", now=now) == parse_date_stamp("3d", now=now)


def test_parse_date_stamp_unrecognized_returns_none():
    assert parse_date_stamp("Just posted") is None
    assert parse_date_stamp("") is None
    assert parse_date_stamp(None) is None
