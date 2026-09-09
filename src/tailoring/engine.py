"""Phase 2 tailoring engine: produces a tailored resume PDF for one job
and stores it — locally always, and to S3 as well when
STORAGE_BACKEND=aws — returning the S3 key (if any) to attach to the
job's Application record.

Two modes, auto-selected per job:
  - LLM-assisted (when ANTHROPIC_API_KEY is set): asks Claude
    (claude_client.py) to rewrite the summary and bullet PHRASING to
    mirror the job description. GUARDRAIL enforced here, not just in the
    prompt: the number of bullets returned per experience entry must
    exactly match the original, or the whole LLM result is discarded and
    this falls back to deterministic mode for that job. Company names,
    titles, dates, and education are never sent to Claude at all, so
    there's no code path where they could come back altered.
  - Deterministic (always available, the fallback): skills reordered to
    match the job, nothing rewritten. See pdf_renderer.py.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional, TypedDict

from src.common.resume_schema import MasterResume
from src.tailoring import claude_client
from src.tailoring.pdf_renderer import render_resume_pdf

MASTER_RESUME_PATH = Path("resume/master_resume.json")
LOCAL_OUTPUT_DIR = Path("resume/output")

# Where scripts/upload_master_resume.py puts a copy in the private S3
# bucket (ResumeBucket, already blocked-public + SSE-encrypted — see
# infra/aws/template.yaml) so the on-demand "Tailor Resume" button also
# works from the AWS-hosted dashboard, not just locally — the Lambda has
# no local resume/master_resume.json (it's git-ignored PII, never staged
# into the Lambda package by prepare_lambda_src.py). Only read here when
# the local file genuinely doesn't exist, so a real local run is never
# affected by this at all.
MASTER_RESUME_S3_KEY = os.environ.get("MASTER_RESUME_S3_KEY", "private/master_resume.json")

_s3_resume_cache: Optional[MasterResume] = None  # per-warm-Lambda-container cache


class TailoringResult(TypedDict):
    local_path: str
    s3_key: Optional[str]
    llm_tailored: bool  # True if Claude's rewrite passed the guardrail and was used


def load_master_resume(path: Path = MASTER_RESUME_PATH) -> MasterResume:
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        return MasterResume(**data)
    if os.environ.get("STORAGE_BACKEND", "local").lower() == "aws":
        return _load_master_resume_from_s3()
    raise FileNotFoundError(
        f"{path} not found — it's git-ignored (real PII) and must exist locally. "
        "See TECHNICAL_PLAN.txt Phase 0."
    )


def _load_master_resume_from_s3() -> MasterResume:
    global _s3_resume_cache
    if _s3_resume_cache is not None:
        return _s3_resume_cache

    import boto3

    bucket = os.environ["S3_BUCKET_NAME"]
    region = os.environ.get("AWS_REGION", "us-east-1")
    response = boto3.client("s3", region_name=region).get_object(
        Bucket=bucket, Key=MASTER_RESUME_S3_KEY
    )
    data = json.loads(response["Body"].read().decode("utf-8"))
    _s3_resume_cache = MasterResume(**data)
    return _s3_resume_cache


def _validate_llm_content(resume: MasterResume, content: claude_client.TailoredContent) -> bool:
    """The guardrail: same number of experience entries, and same bullet
    COUNT per entry, as the original. Anything else means we don't trust
    the response enough to use it — better a safe deterministic resume
    than a tailored one that might have dropped or merged an achievement."""
    bullets = content.get("experience_bullets")
    if not isinstance(bullets, list) or len(bullets) != len(resume.experience):
        return False
    for original_entry, tailored_bullets in zip(resume.experience, bullets):
        if not isinstance(tailored_bullets, list):
            return False
        if len(tailored_bullets) != len(original_entry.bullets):
            return False
        if not all(isinstance(b, str) and b.strip() for b in tailored_bullets):
            return False
    return bool(content.get("summary", "").strip())


def _local_output_dir() -> Path:
    # The repo checkout is read-only inside a running Lambda (only /tmp is
    # writable there) - this only matters once tailor_resume_for_job can
    # run from the dashboard's on-demand "Tailor Resume" button, not just
    # the local ingest pipeline. The write below is disposable either way
    # on AWS: download_resume() (src/dashboard/app.py) always prefers the
    # S3 copy over a local file when one exists.
    if "AWS_LAMBDA_FUNCTION_NAME" in os.environ:
        return Path("/tmp/resume_output")
    return LOCAL_OUTPUT_DIR


def tailor_resume_for_job(
    job_id: str,
    job_title: str = "",
    job_description: Optional[str] = None,
    required_skills: Optional[list[str]] = None,
) -> TailoringResult:
    """Generates and stores the tailored PDF for one job. Always writes a
    local copy (resume/output/<job_id>.pdf, git-ignored — or /tmp on
    Lambda, see _local_output_dir); additionally uploads to S3 when
    STORAGE_BACKEND=aws is set, returning that key so the caller can
    attach it to the job's Application.resume_s3_key."""
    resume = load_master_resume()

    tailored_summary = None
    tailored_bullets = None
    llm_tailored = False

    if job_description and claude_client.is_available():
        content = claude_client.request_tailored_content(resume, job_title, job_description)
        if content and _validate_llm_content(resume, content):
            tailored_summary = content["summary"]
            tailored_bullets = content["experience_bullets"]
            llm_tailored = True
        # else: content was None (API/config issue) or failed the guardrail
        # (e.g. wrong bullet count) — silently fall back to deterministic,
        # nothing to do here, tailored_summary/tailored_bullets stay None.

    pdf_bytes = render_resume_pdf(
        resume,
        required_skills=required_skills,
        tailored_summary=tailored_summary,
        tailored_experience_bullets=tailored_bullets,
    )

    output_dir = _local_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    local_path = output_dir / f"{job_id}.pdf"
    local_path.write_bytes(pdf_bytes)

    s3_key: Optional[str] = None
    if os.environ.get("STORAGE_BACKEND", "local").lower() == "aws":
        import boto3

        bucket = os.environ["S3_BUCKET_NAME"]
        region = os.environ.get("AWS_REGION", "us-east-1")
        s3_key = f"resumes/{job_id}.pdf"
        boto3.client("s3", region_name=region).put_object(
            Bucket=bucket, Key=s3_key, Body=pdf_bytes, ContentType="application/pdf"
        )

    return {"local_path": str(local_path), "s3_key": s3_key, "llm_tailored": llm_tailored}
