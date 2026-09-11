"""Shared scraper infrastructure: a persistent browser context and
randomized human-like delays. Every site-specific scraper (simplyhired.py
now; indeed.py, linkedin.py later) builds on this so the anti-ban
behavior described in TECHNICAL_PLAN.txt section 6 lives in one place
instead of being re-implemented (and drifting) per site.
"""

from __future__ import annotations

import random
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from playwright.sync_api import BrowserContext, sync_playwright

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

# Text markers seen on real bot-challenge/interstitial pages, used by
# looks_blocked() below. Verified live 2026-09-11: a plain headless
# Playwright request to Indeed's search page got a real 200 with real job
# cards on the FIRST request, then a 403 "Security Check" interstitial on
# the very next request seconds later — Indeed's bot detection is far more
# aggressive than SimplyHired's (which has none) or LinkedIn's guest search
# (which tolerates plain GETs). Every multi-source scraper must treat a
# block as an expected, non-fatal outcome for that one request — log it and
# move on — never crash the whole ingest run over it.
#
# Deliberately NOT included: a bare "captcha". Verified live the same
# session — a real, fully-legitimate LinkedIn results page (200, real job
# cards, title "30,000+ React Developer jobs in Remote") contains that
# substring on its own, buried in an A/B-test config attribute
# (`data-recaptcha-v3-integration-lix-value="control"`) that has nothing
# to do with an actual challenge being shown. That one false positive
# would have silently thrown away every real LinkedIn search. Every
# marker actually kept below was checked against that same real page and
# confirmed to NOT appear on it, specifically to avoid a repeat of this.
BLOCK_MARKERS = [
    "security check",
    "additional verification required",
    "unusual traffic",
    "access denied",
    "are you a human",
    "verify you are a human",
    "just a moment",  # Cloudflare's interstitial title
    "px-captcha",  # PerimeterX's actual challenge widget id, not the bare word
    "bot-detection",  # Indeed's own login-redirect flavor of block, verified live
    # 2026-09-11: a job-detail fetch (not the search page itself) came
    # back as a 735-byte "Authenticating..." stub that JS-redirects to
    # /account/login?...&from=bot-detection-anonymous — no visible
    # "captcha"/"security check" wording at all, so it needed its own
    # marker. Confirmed absent from real LinkedIn/Indeed results pages.
]


def looks_blocked(html: str) -> bool:
    """Best-effort check for a bot-challenge/interstitial page instead of
    real search results — see BLOCK_MARKERS. Not authoritative (a real
    page could coincidentally contain one of these phrases, and a novel
    challenge page might use none of them), but good enough to decide
    "skip this request and log it" vs. "parse it as normal results"."""
    haystack = html.lower()
    return any(marker in haystack for marker in BLOCK_MARKERS)


def human_delay(min_seconds: float = 1.5, max_seconds: float = 4.0) -> None:
    """Randomized pause between actions. Per TECHNICAL_PLAN.txt section 6,
    a FIXED delay is itself a detectable pattern — every wait in the
    scrapers (and later the apply engine) should go through this rather
    than a bare time.sleep()."""
    time.sleep(random.uniform(min_seconds, max_seconds))


@contextmanager
def browser_context(
    storage_state_path: Optional[Path] = None,
    headless: bool = True,
) -> Iterator[BrowserContext]:
    """Launches a browser context, optionally resuming a persisted
    session (cookies/localStorage) from storage_state_path so repeated
    runs look like a returning user rather than a fresh bot each time —
    see TECHNICAL_PLAN.txt section 6, factor #2 (fingerprint). Writes the
    session back out on exit so the next run can resume it.

    storage_state_path is None for sites that don't need login (e.g. a
    plain SimplyHired search) — pass a real path for anything that logs
    in (LinkedIn/Indeed, Phase 4/5), and NEVER commit that file (see
    .gitignore: storage_state.json).
    """
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(
            user_agent=DEFAULT_USER_AGENT,
            viewport={"width": 1366, "height": 900},
            storage_state=(
                str(storage_state_path)
                if storage_state_path and storage_state_path.exists()
                else None
            ),
        )
        # Minimal stealth: hide the most obvious automation flag. Full
        # stealth patching (playwright-stealth or equivalent) is a
        # follow-up before this touches LinkedIn/Indeed login flows — see
        # TECHNICAL_PLAN.txt Phase 4.
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
        )
        try:
            yield context
        finally:
            if storage_state_path:
                storage_state_path.parent.mkdir(parents=True, exist_ok=True)
                context.storage_state(path=str(storage_state_path))
            context.close()
            browser.close()
