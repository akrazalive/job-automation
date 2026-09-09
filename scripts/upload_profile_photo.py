"""Uploads resume/photo.jpg (a real headshot, git-ignored PII) to the
private/encrypted S3 bucket so the AWS-hosted dashboard's on-demand
"Tailor Resume" button can render it into the PDF header too, not just
the local dashboard - see src/tailoring/engine.py's load_profile_photo()
/ _load_photo_from_s3().

Run locally, once, and again any time you replace the photo:

    $env:AWS_REGION="us-east-1"
    $env:S3_BUCKET_NAME="job-automation-resumes-607581913131-prod"
    python scripts/upload_profile_photo.py

Same bucket/prefix pattern as scripts/upload_master_resume.py - a
"private/" key in the same bucket that already holds tailored PDFs
under "resumes/", already blocking all public access and encrypting at
rest (see infra/aws/template.yaml's ResumeBucket). The Lambda's IAM role
can only GetObject there (read) via S3ReadPolicy on the whole bucket -
it has no route or permission to overwrite this file, so this script is
the only way the live copy changes.
"""

from __future__ import annotations

import os
from pathlib import Path

import boto3

REPO_ROOT = Path(__file__).resolve().parent.parent
PHOTO_PATH = REPO_ROOT / "resume" / "photo.jpg"
DEFAULT_S3_KEY = "private/photo.jpg"


def main() -> None:
    if not PHOTO_PATH.exists():
        raise SystemExit(f"{PHOTO_PATH} not found - nothing to upload.")

    bucket = os.environ.get("S3_BUCKET_NAME")
    if not bucket:
        raise SystemExit("Set S3_BUCKET_NAME first (see this script's docstring).")
    region = os.environ.get("AWS_REGION", "us-east-1")
    key = os.environ.get("PROFILE_PHOTO_S3_KEY", DEFAULT_S3_KEY)

    data = PHOTO_PATH.read_bytes()
    boto3.client("s3", region_name=region).put_object(
        Bucket=bucket, Key=key, Body=data, ContentType="image/jpeg",
        ServerSideEncryption="AES256",
    )
    print(f"Uploaded {PHOTO_PATH} ({len(data)} bytes) -> s3://{bucket}/{key}")


if __name__ == "__main__":
    main()
