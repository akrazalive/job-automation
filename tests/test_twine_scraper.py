from datetime import datetime, timezone
from pathlib import Path

from src.common.dedupe import job_id_for
from src.scrapers.base import looks_blocked
from src.scrapers.twine import (
    build_search_url,
    parse_date_relative,
    parse_job_detail,
    parse_search_results,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_build_search_url_ignores_query_and_location():
    # Twine has no working keyword-search URL - every call resolves to
    # the same "Web Developer Jobs" category page regardless of what's
    # passed in. See module docstring for why.
    assert build_search_url("Full Stack Developer", "Remote") == "https://www.twine.net/jobs/web-developers"
    assert build_search_url() == "https://www.twine.net/jobs/web-developers"


def test_parse_search_results():
    html = (FIXTURES / "twine_search.html").read_text(encoding="utf-8")
    rows = parse_search_results(html)
    # 2 real cards in the fixture; the 3rd (no wrapping title div) must be skipped.
    assert len(rows) == 2

    assert rows[0].title == "Full Stack Developer"
    assert rows[0].company == "Acme Corp"
    assert rows[0].location == "Remote"
    assert rows[0].detail_url == "https://www.twine.net/projects/abc123-full-stack-developer-remote-job"
    assert rows[0].date_text == "a day ago"

    # Anonymous-client card falls back to "Unknown", same convention as
    # the other three scrapers' missing-company handling.
    assert rows[1].title == "Python/Django Backend Developer [Searching]"
    assert rows[1].company == "Unknown"
    assert rows[1].location == "India"
    assert rows[1].date_text == "7 days ago"


def test_parse_search_results_dedupes_the_titleanddescription_anchor_pair():
    # Each real card links its title AND its "Read more" description
    # snippet to the identical /projects/ URL - parse_search_results()
    # must produce exactly one row per job, not two.
    html = (FIXTURES / "twine_search.html").read_text(encoding="utf-8")
    rows = parse_search_results(html)
    urls = [r.detail_url for r in rows]
    assert len(urls) == len(set(urls))


def test_parse_job_detail():
    html = (FIXTURES / "twine_job_detail.html").read_text(encoding="utf-8")
    description = parse_job_detail(html)
    assert "Full Stack Developer with experience in React" in description
    assert "CI/CD pipelines" in description


def test_parse_job_detail_missing_section_returns_empty():
    assert parse_job_detail("<html><body>nothing here</body></html>") == ""


def test_job_id_is_deterministic_and_source_prefixed():
    a = job_id_for("twine", "Acme Corp", "Full Stack Developer", "https://x/projects/1")
    b = job_id_for("twine", "Acme Corp", "Full Stack Developer", "https://x/projects/1")
    c = job_id_for("twine", "Acme Corp", "Full Stack Developer", "https://x/projects/2")
    assert a == b
    assert a != c
    assert a.startswith("twine-")


def test_parse_date_relative_hours():
    now = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)
    assert parse_date_relative("3 hours ago", now=now) == datetime(2026, 9, 12, 9, 0, tzinfo=timezone.utc)


def test_parse_date_relative_days():
    now = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)
    assert parse_date_relative("4 days ago", now=now) == datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)


def test_parse_date_relative_singular_a_an():
    now = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)
    assert parse_date_relative("a day ago", now=now) == datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)
    assert parse_date_relative("an hour ago", now=now) == datetime(2026, 9, 12, 11, 0, tzinfo=timezone.utc)


def test_parse_date_relative_case_insensitive_and_whitespace():
    now = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)
    assert parse_date_relative(" 4 DAYS AGO ", now=now) == parse_date_relative("4 days ago", now=now)


def test_parse_date_relative_unrecognized_returns_none():
    assert parse_date_relative("just now") is None
    assert parse_date_relative("") is None
    assert parse_date_relative(None) is None


def test_looks_blocked_false_for_real_results_page():
    html = (FIXTURES / "twine_search.html").read_text(encoding="utf-8")
    assert looks_blocked(html) is False
