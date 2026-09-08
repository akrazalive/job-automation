from pathlib import Path

from src.common.dedupe import job_id_for
from src.scrapers.simplyhired import build_search_url, parse_job_detail, parse_search_results

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
    assert rows[1].company == "Globex Inc"


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
