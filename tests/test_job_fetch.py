from src.tailoring.job_fetch import (
    JobPosting,
    _clean_page_title,
    _guess_company_from_url,
    fetch_job_posting,
    fetch_job_text,
)


def test_clean_page_title_strips_known_site_suffix():
    assert _clean_page_title("Senior Backend Developer | LinkedIn") == "Senior Backend Developer"
    assert _clean_page_title("Senior Backend Developer - Indeed.com") == "Senior Backend Developer"


def test_clean_page_title_leaves_unknown_suffix_alone():
    # Must NOT chop a real title just because it happens to contain a
    # separator - only a KNOWN job-board site name after the last
    # separator is stripped (see _KNOWN_JOB_SITE_NAMES).
    assert _clean_page_title("Full-Stack Developer - Acme Corp") == "Full-Stack Developer - Acme Corp"


def test_clean_page_title_collapses_whitespace():
    assert _clean_page_title("  Senior   Developer  \n Role  ") == "Senior Developer Role"


def test_guess_company_from_url_strips_www():
    assert _guess_company_from_url("https://www.example.com/jobs/123") == "example.com"


def test_guess_company_from_url_keeps_subdomain():
    assert _guess_company_from_url("https://boards.greenhouse.io/acme/jobs/1") == "boards.greenhouse.io"


def test_guess_company_from_url_falls_back_to_unknown_for_garbage_input():
    assert _guess_company_from_url("not-a-url") == "Unknown"


def test_fetch_job_posting_returns_none_when_fetch_fails(monkeypatch):
    monkeypatch.setattr("src.tailoring.job_fetch._fetch_html", lambda url, timeout: None)
    assert fetch_job_posting("https://example.com/job") is None


def test_fetch_job_posting_extracts_title_and_text(monkeypatch):
    html = b"""
    <html><head><title>Senior React Developer | LinkedIn</title></head>
    <body><h1>Senior React Developer</h1><p>We need React and AWS experience.</p></body></html>
    """
    monkeypatch.setattr("src.tailoring.job_fetch._fetch_html", lambda url, timeout: html)

    posting = fetch_job_posting("https://www.linkedin.com/jobs/view/12345")
    assert isinstance(posting, JobPosting)
    assert posting.title_guess == "Senior React Developer"
    assert posting.company_guess == "linkedin.com"
    assert "React" in posting.text
    assert "AWS" in posting.text


def test_fetch_job_posting_handles_missing_title_tag(monkeypatch):
    html = b"<html><body><p>Some job text with no title tag at all.</p></body></html>"
    monkeypatch.setattr("src.tailoring.job_fetch._fetch_html", lambda url, timeout: html)

    posting = fetch_job_posting("https://example.com/job")
    assert posting.title_guess is None
    assert "Some job text" in posting.text


def test_fetch_job_posting_returns_none_when_page_has_no_extractable_text(monkeypatch):
    # No <title> and nothing but whitespace in <body> - soup.get_text()
    # (which doesn't exclude <head>, only the explicitly-removed
    # script/style/noscript tags) has nothing left to return at all.
    html = b"<html><head></head><body>   </body></html>"
    monkeypatch.setattr("src.tailoring.job_fetch._fetch_html", lambda url, timeout: html)
    assert fetch_job_posting("https://example.com/job") is None


def test_fetch_job_text_still_works_after_refactor(monkeypatch):
    # fetch_job_text shares _fetch_html with fetch_job_posting now -
    # regression check that the refactor didn't change its own behavior.
    html = b"<html><body><p>Looking for a Python and Django expert.</p></body></html>"
    monkeypatch.setattr("src.tailoring.job_fetch._fetch_html", lambda url, timeout: html)

    text = fetch_job_text("https://example.com/job")
    assert "Python" in text and "Django" in text
