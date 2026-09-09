"""Uploads resume/master_resume.json (real PII - name, email, phone,
address, git-ignored) to the private/encrypted S3 bucket so the AWS-hosted
dashboard's on-demand "Tailor Resume" button can render PDFs too, not just
the local dashboard - see src/tailoring/engine.py's
_load_master_resume_from_s3().

Run locally, once, and again any time master_resume.json changes (a new
job, a reworded bullet, etc.) so the live copy stays in sync:

    $env:AWS_REGION="us-east-1"
    $env:S3_BUCKET_NAME="job-automation-resumes-607581913131-prod"
    python scripts/upload_master_resume.py

The upload target is a "private/" prefix within the SAME bucket that
already holds tailored PDFs under "resumes/" - that bucket already blocks
all public access and encrypts at rest (see infra/aws/template.yaml's
ResumeBucket). The Lambda's IAM role can only GetObject there (read), via
S3ReadPolicy on the whole bucket - it has no route or permission that
would let it overwrite this file, so this script is the only way the
live copy changes.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import boto3

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))  # run as `python scripts/upload_master_resume.py` from anywhere - needs src/ importable

from src.common.resume_schema import MasterResume  # noqa: E402

MASTER_RESUME_PATH = REPO_ROOT / "resume" / "master_resume.json"
DEFAULT_S3_KEY = "private/master_resume.json"


def main() -> None:
    if not MASTER_RESUME_PATH.exists():
        raise SystemExit(f"{MASTER_RESUME_PATH} not found - nothing to upload.")

    bucket = os.environ.get("S3_BUCKET_NAME")
    if not bucket:
        raise SystemExit("Set S3_BUCKET_NAME first (see this script's docstring).")
    region = os.environ.get("AWS_REGION", "us-east-1")
    key = os.environ.get("MASTER_RESUME_S3_KEY", DEFAULT_S3_KEY)

    raw = MASTER_RESUME_PATH.read_text(encoding="utf-8")
    # Validate against the real schema before uploading - catches a typo'd
    # local edit here instead of it silently breaking the live "Tailor
    # Resume" button the next time someone clicks it on the AWS dashboard.
    MasterResume(**json.loads(raw))

    boto3.client("s3", region_name=region).put_object(
        Bucket=bucket, Key=key, Body=raw.encode("utf-8"), ContentType="application/json",
        ServerSideEncryption="AES256",
    )
    print(f"Uploaded {MASTER_RESUME_PATH} -> s3://{bucket}/{key}")


if __name__ == "__main__":
    main()
