from pathlib import Path

from src.common.dedupe import job_id_for
from src.scrapers.base import looks_blocked
from src.scrapers.indeed import build_search_url, parse_job_detail, parse_search_results

FIXTURES = Path(__file__).parent / "fixtures"


def test_build_search_url():
    url = build_search_url("Full Stack Developer", "Remote")
    assert url.startswith("https://www.indeed.com/jobs?")
    assert "q=Full+Stack+Developer" in url
    assert "l=Remote" in url


def test_build_search_url_no_location():
    url = build_search_url("Full Stack Developer")
    assert "l=" not in url


def test_build_search_url_pagination():
    url = build_search_url("Full Stack Developer", "Remote", page=2)
    assert "start=20" in url


def test_parse_search_results():
    html = (FIXTURES / "indeed_search.html").read_text(encoding="utf-8")
    rows = parse_search_results(html)
    # 3 cards in the fixture, but the 3rd (no data-jk) must be skipped.
    assert len(rows) == 2
    assert rows[0].title == "Full Stack Developer"
    assert rows[0].company == "Acme Corp"
    assert rows[0].location == "Remote"
    assert rows[0].detail_url == "https://www.indeed.com/viewjob?jk=abc123"
    assert rows[1].company == "Globex Inc"
    assert rows[1].detail_url == "https://www.indeed.com/viewjob?jk=def456"


def test_parse_job_detail():
    html = (FIXTURES / "indeed_job_detail.html").read_text(encoding="utf-8")
    description = parse_job_detail(html)
    assert "Full Stack Developer with experience in React" in description
    assert "CI/CD pipelines" in description


def test_parse_job_detail_missing_section_returns_empty():
    assert parse_job_detail("<html><body>nothing here</body></html>") == ""


def test_job_id_is_deterministic_and_source_prefixed():
    a = job_id_for("indeed", "Acme Corp", "Full Stack Developer", "https://x/viewjob?jk=1")
    b = job_id_for("indeed", "Acme Corp", "Full Stack Developer", "https://x/viewjob?jk=1")
    c = job_id_for("indeed", "Acme Corp", "Full Stack Developer", "https://x/viewjob?jk=2")
    assert a == b
    assert a != c
    assert a.startswith("indeed-")


def test_looks_blocked_detects_security_check_page():
    html = "<html><head><title>Security Check - Indeed.com</title></head><body>Additional Verification Required</body></html>"
    assert looks_blocked(html) is True


def test_looks_blocked_detects_indeed_login_redirect_stub():
    # Verified live 2026-09-11: a blocked job-DETAIL fetch (distinct from
    # the search-results block above) came back as this exact short
    # "Authenticating..." stub instead of a real "Security Check" page —
    # see src/scrapers/base.py's BLOCK_MARKERS comment.
    html = (
        '<html><head><title>Authenticating...</title></head><body>'
        'window.location.replace("https://www.indeed.com/account/login'
        '?branding=login-required&from=bot-detection-anonymous&continue=...")'
        "</body></html>"
    )
    assert looks_blocked(html) is True


def test_looks_blocked_false_for_real_results_page():
    html = (FIXTURES / "indeed_search.html").read_text(encoding="utf-8")
    assert looks_blocked(html) is False
