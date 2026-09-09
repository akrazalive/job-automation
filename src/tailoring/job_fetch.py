"""Best-effort live fetch of a job posting's page text, used only to
refresh the skills used for an on-demand "Tailor Resume" click (see
src/dashboard/app.py's POST /api/applications/{job_id}/tailor).

Plain HTTP GET + BeautifulSoup text extraction — no Playwright/browser
here, deliberately: this needs to run from a dashboard request (including
on the AWS Lambda dashboard, which can't run a browser), not just as part
of the local scrape pipeline. That means it does NOT reliably get past
LinkedIn/Indeed's JS rendering or login walls the way the real Playwright
scraper (src/scrapers/, local-only) does — this is fine, because the
tailoring engine always has a solid baseline regardless: whatever skills
the real scraper already found at scrape time
(Application.required_skills) are never discarded, this only adds
anything extra a fresh fetch turns up. A blocked/failed/timed-out fetch
degrades to "no extra signal found" — never an error, never blocks
tailoring.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from typing import Optional

from bs4 import BeautifulSoup

# A realistic desktop-browser UA. Not trying to evade detection (this
# isn't scraping to apply, just a one-off read of one already-known URL a
# human clicked "Tailor" on) - just avoiding an instant 403 from sites
# that block obviously-non-browser default User-Agents like "Python-urllib/3.x".
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
_MAX_BYTES = 2_000_000  # plenty for a job posting page; caps a runaway response


def fetch_job_text(url: str, timeout: float = 8.0) -> Optional[str]:
    """Returns extracted visible text from `url`, or None on any failure
    (blocked, timeout, non-200, non-HTML, redirected to a login wall that
    still returns 200, etc.) — always best-effort, never raises."""
    try:
        request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status != 200:
                return None
            content_type = response.headers.get("Content-Type", "")
            if "html" not in content_type.lower():
                return None
            raw = response.read(_MAX_BYTES)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None

    try:
        soup = BeautifulSoup(raw, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        text = soup.get_text(separator=" ", strip=True)
        return text or None
    except Exception:
        return None
