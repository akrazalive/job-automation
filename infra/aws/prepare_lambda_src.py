"""Stages a minimal, explicit source tree for the dashboard Lambda before
`sam build`.

This exists because relying on .samignore to exclude personal files (the
resume, .env, an AWS credentials CSV, etc.) from the packaged CodeUri
turned out NOT to be safe in practice: verified empirically on 2026-09-08
that a plain `sam build` with CodeUri pointed at the repo root copied the
ENTIRE repo — real resume .docx, master_resume.json (name/email/phone/
address), and an AWS access key CSV that happened to be sitting in the
repo folder — into the local build artifact, .samignore notwithstanding.
That artifact is exactly what `sam deploy` uploads to AWS.

Explicit allow-listing here is safer than continuing to debug ignore-file
heuristics: nothing lands in the staged directory (.lambda_src/, itself
git-ignored) unless this script puts it there. Run this before every
`sam build`:

    python infra/aws/prepare_lambda_src.py
    sam build
    sam deploy --guided
"""

from __future__ import annotations

import shutil
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
STAGING_DIR = Path(__file__).resolve().parent / ".lambda_src"

# Exactly what the dashboard Lambda needs to run. Deliberately NOT
# including src/scrapers (playwright, local-only per TECHNICAL_PLAN.txt
# section 6 — real anti-bot browser automation has no place running from
# a datacenter IP). src/tailoring IS included: the dashboard's on-demand
# "Tailor Resume" button (POST /api/applications/{job_id}/tailor) calls
# into it directly, rendering a fresh PDF inside the Lambda itself
# (reportlab is pure Python — no system deps needed, works fine here) —
# see src/tailoring/engine.py's S3 fallback for master_resume.json, since
# the real git-ignored local file obviously isn't staged into this
# package either.
SRC_PACKAGES = ["common", "storage", "dashboard", "tailoring"]

# A slimmer requirements.txt than the repo's main one: the Lambda never
# needs playwright (scraper-only, and src/scrapers isn't even staged —
# see SRC_PACKAGES above) or uvicorn (Mangum replaces uvicorn's serving
# role under Lambda). reportlab + beautifulsoup4 ARE needed here (unlike
# the earlier version of this list) for on-demand PDF tailoring —
# beautifulsoup4 uses only the stdlib "html.parser" backend
# (src/tailoring/job_fetch.py), no lxml needed. pyyaml, python-multipart,
# and itsdangerous ARE needed even though the settings-editing *routes*
# are local-only (403 on Lambda) — app.py imports yaml/itsdangerous at
# module load time, and the login form needs python-multipart, so these
# must be present for the Lambda to import successfully at all.
LAMBDA_REQUIREMENTS = """\
fastapi>=0.115
mangum>=0.19
boto3>=1.35
pydantic>=2.9
jinja2>=3.1
pyyaml>=6.0
python-multipart>=0.0.12
itsdangerous>=2.2
reportlab>=4.2
beautifulsoup4>=4.12
"""


def main() -> None:
    if STAGING_DIR.exists():
        shutil.rmtree(STAGING_DIR)
    STAGING_DIR.mkdir(parents=True)

    src_dir = STAGING_DIR / "src"
    src_dir.mkdir()
    (src_dir / "__init__.py").write_text("", encoding="utf-8")

    for package in SRC_PACKAGES:
        source = REPO_ROOT / "src" / package
        shutil.copytree(
            source, src_dir / package, ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
        )

    (STAGING_DIR / "requirements.txt").write_text(LAMBDA_REQUIREMENTS, encoding="utf-8")

    files = sorted(p.relative_to(STAGING_DIR) for p in STAGING_DIR.rglob("*") if p.is_file())
    print(f"Staged {len(files)} files at {STAGING_DIR}:")
    for f in files:
        print(f"  {f}")


if __name__ == "__main__":
    main()
