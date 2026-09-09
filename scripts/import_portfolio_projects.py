"""Imports REAL projects from the operator's own live portfolio page
(https://engrasiflive.vercel.app/portfolio-projects.html) into
resume/master_resume.json's `projects` list, so the tailoring engine can
select among them per job.

Why this exists / how it's different from project_bank.json: the project
bank (project_bank.json, 255 entries) is AI-GENERATED example ideas -
never claimed as real work, and explicitly walled off from the actual
tailoring pipeline (see src/common/project_bank.py's module docstring).
THIS script imports a different, verified-real data source: the
operator's own portfolio page, which lists actual completed client work
with live URLs to real production sites (Zscaler, BNP Paribas, AUC,
Richemont, MSC Cruises, and 100+ more, spanning WordPress/WooCommerce,
Laravel, Symfony, CodeIgniter, Yii, Joomla, Magento, React/Next.js,
Django, ASP.NET, and mobile). Real projects belong in
resume/master_resume.json (protected, git-ignored, PROTECTED_* fields
never touched by tailoring) - never in project_bank.json.

Usage (from repo root):

    python scripts/import_portfolio_projects.py               # merge (default)
    python scripts/import_portfolio_projects.py --dry-run      # preview only, no write
    python scripts/import_portfolio_projects.py --url <other>  # a different portfolio page

Merge behavior: upserts by title (case-insensitive) - re-running after
the portfolio page adds new entries only adds the new ones; existing
entries (including any you've hand-edited since import) are left alone
unless their title exactly matches an incoming one, in which case the
incoming (portfolio) version wins for description/tech/url, since the
portfolio is the source of truth for this data. Never deletes a project
that's only in master_resume.json and not (yet) on the portfolio page -
run with --replace-all only if you specifically want that.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.common.resume_schema import MasterResume, ProjectEntry  # noqa: E402

DEFAULT_PORTFOLIO_URL = "https://engrasiflive.vercel.app/portfolio-projects.html"
MASTER_RESUME_PATH = REPO_ROOT / "resume" / "master_resume.json"

_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def fetch_portfolio_html(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request, timeout=15) as response:
        return response.read().decode("utf-8")


def _js_string_to_json(match: re.Match) -> str:
    """Converts one single-quoted JS string literal's inner text to a
    valid double-quoted JSON string: un-escapes \\' (JS only needs to
    escape a quote matching the string's own delimiter) and escapes any
    literal " so json.loads doesn't choke on it."""
    inner = match.group(1)
    inner = inner.replace("\\'", "'")
    inner = inner.replace('"', '\\"')
    return '"' + inner + '"'


_KNOWN_KEYS = ("title", "url", "desc", "tags", "filters", "color", "mobile")


def _js_object_line_to_json(line: str) -> str:
    for key in _KNOWN_KEYS:
        line = re.sub(rf"(?<=[{{,])({key}):", rf'"{key}":', line)
    line = re.sub(r"'((?:[^'\\]|\\.)*)'", _js_string_to_json, line)
    return line


def parse_portfolio_projects(html: str) -> list[dict]:
    """Extracts the `const PROJECTS = [...]` JS array and parses each
    `{title: '...', ...}` entry into a plain dict. Each object is on its
    own source line (verified against the live page) - simpler and more
    robust than brace-matching across a regex, which broke on the final
    entry's trailing `mobile:true` field during development."""
    match = re.search(r"const PROJECTS = \[(.*?)\n\];", html, re.S)
    if not match:
        raise ValueError("Could not find `const PROJECTS = [...]` in the fetched page — layout may have changed.")
    lines = [ln.strip().rstrip(",") for ln in match.group(1).split("\n") if ln.strip().startswith("{title:")]

    records = []
    for line in lines:
        converted = _js_object_line_to_json(line)
        records.append(json.loads(converted))
    return records


def to_project_entry(raw: dict) -> ProjectEntry:
    tech = raw.get("tags", [])
    url = raw.get("url")
    if url == "#":
        url = None  # private/NDA project on the portfolio page - no public link
    return ProjectEntry(name=raw["title"], description=raw["desc"], tech=tech, url=url)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default=DEFAULT_PORTFOLIO_URL, help="Portfolio page to import from.")
    parser.add_argument("--dry-run", action="store_true", help="Parse and report, but don't write master_resume.json.")
    args = parser.parse_args()

    if not MASTER_RESUME_PATH.exists():
        raise SystemExit(f"{MASTER_RESUME_PATH} not found — nothing to merge into.")

    html = fetch_portfolio_html(args.url)
    raw_records = parse_portfolio_projects(html)
    print(f"Parsed {len(raw_records)} projects from {args.url}")

    incoming = [to_project_entry(r) for r in raw_records]

    data = json.loads(MASTER_RESUME_PATH.read_text(encoding="utf-8"))
    resume = MasterResume(**data)

    existing_by_title = {p.name.strip().lower(): i for i, p in enumerate(resume.projects)}
    added, updated = 0, 0
    for entry in incoming:
        key = entry.name.strip().lower()
        if key in existing_by_title:
            resume.projects[existing_by_title[key]] = entry
            updated += 1
        else:
            resume.projects.append(entry)
            existing_by_title[key] = len(resume.projects) - 1
            added += 1

    print(f"{added} new projects, {updated} updated, {len(resume.projects)} total after merge.")

    if args.dry_run:
        print("--dry-run: not writing.")
        return

    MASTER_RESUME_PATH.write_text(
        json.dumps(json.loads(resume.model_dump_json()), indent=2), encoding="utf-8"
    )
    print(f"Wrote {MASTER_RESUME_PATH}")
    print("Remember: if you also run the dashboard against AWS, re-sync with "
          "`python scripts/upload_master_resume.py` so the live Lambda sees these too.")


if __name__ == "__main__":
    main()
