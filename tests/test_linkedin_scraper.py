from datetime import datetime, timezone
from pathlib import Path

from src.common.dedupe import job_id_for
from src.scrapers.base import looks_blocked
from src.scrapers.linkedin import (
    build_search_url,
    parse_date_iso,
    parse_job_detail,
    parse_search_results,
)

FIXTURES = Path(__file__).parent / "fixtures"


def test_build_search_url():
    url = build_search_url("Full Stack Developer", "Remote")
    assert url.startswith("https://www.linkedin.com/jobs/search?")
    assert "keywords=Full+Stack+Developer" in url
    assert "location=Remote" in url


def test_build_search_url_no_location():
    url = build_search_url("Full Stack Developer")
    assert "location=" not in url


def test_build_search_url_pagination():
    url = build_search_url("Full Stack Developer", "Remote", page=2)
    assert "start=50" in url


def test_parse_search_results():
    html = (FIXTURES / "linkedin_search.html").read_text(encoding="utf-8")
    rows = parse_search_results(html)
    # 3 cards in the fixture, but the 3rd (no full-link href) must be skipped.
    assert len(rows) == 2
    assert rows[0].title == "Full Stack Developer"
    assert rows[0].company == "Acme Corp"
    assert rows[0].location == "Remote"
    # tracking query string stripped, path kept
    assert rows[0].detail_url == "https://www.linkedin.com/jobs/view/full-stack-developer-at-acme-corp-1001"
    assert rows[0].date_text == "2026-09-08"
    assert rows[1].company == "Globex Inc"
    assert rows[1].date_text == "2026-08-01"


def test_parse_job_detail():
    html = (FIXTURES / "linkedin_job_detail.html").read_text(encoding="utf-8")
    description = parse_job_detail(html)
    assert "Full Stack Developer with experience in React" in description
    assert "CI/CD pipelines" in description


def test_parse_job_detail_missing_section_returns_empty():
    assert parse_job_detail("<html><body>nothing here</body></html>") == ""


def test_job_id_is_deterministic_and_source_prefixed():
    a = job_id_for("linkedin", "Acme Corp", "Full Stack Developer", "https://x/jobs/view/1")
    b = job_id_for("linkedin", "Acme Corp", "Full Stack Developer", "https://x/jobs/view/1")
    c = job_id_for("linkedin", "Acme Corp", "Full Stack Developer", "https://x/jobs/view/2")
    assert a == b
    assert a != c
    assert a.startswith("linkedin-")


def test_parse_date_iso():
    assert parse_date_iso("2026-05-28") == datetime(2026, 5, 28, tzinfo=timezone.utc)


def test_parse_date_iso_unrecognized_returns_none():
    assert parse_date_iso("3 months ago") is None
    assert parse_date_iso("") is None
    assert parse_date_iso(None) is None


def test_looks_blocked_detects_challenge_page():
    html = "<html><body>Please verify you are a human to continue.</body></html>"
    assert looks_blocked(html) is True


def test_looks_blocked_does_not_false_positive_on_bare_captcha_word():
    # Regression guard: a real, fully-legitimate LinkedIn results page was
    # found live to contain the bare substring "captcha" in an A/B-test
    # config attribute unrelated to any actual challenge — see
    # src/scrapers/base.py's BLOCK_MARKERS comment. That specific false
    # positive must never come back.
    html = '<html><body><div data-recaptcha-v3-integration-lix-value="control">real results</div></body></html>'
    assert looks_blocked(html) is False


def test_looks_blocked_false_for_real_results_page():
    html = (FIXTURES / "linkedin_search.html").read_text(encoding="utf-8")
    assert looks_blocked(html) is False
