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
