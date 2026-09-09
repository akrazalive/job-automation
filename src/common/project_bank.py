"""Loads project_bank.json and suggests project ideas matching a job's
required skills (src/common/skills.py:extract_skills output).

IMPORTANT — this is an idea generator, not a resume auto-writer. Nothing
here writes into resume/master_resume.json or a tailored PDF. The
intended workflow (see TECHNICAL_PLAN.txt): a job needs skill X -> this
surfaces a couple of realistic X projects you don't have yet -> you
actually build one -> it becomes a real entry in your master resume ->
the existing deterministic tailoring engine (skill-reorder, no AI cost)
can then legitimately pick among your real projects per job. Presenting
a bank project as work you did without building it would be exactly the
"invented projects" the tailoring engine's guardrails are designed to
prevent — don't wire this into src/tailoring/ as if it were real content.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
from pathlib import Path
from typing import Optional

PROJECT_BANK_PATH = Path(__file__).resolve().parent.parent.parent / "project_bank.json"


def load_project_bank(path: Path = PROJECT_BANK_PATH) -> list[dict]:
    """Local file when STORAGE_BACKEND isn't "aws" (the default — matches
    every other storage switch in this project); the DynamoDB table
    (loaded once via scripts/load_project_bank_to_dynamodb.py) when it
    is, so the deployed dashboard reads the same table the JSON was
    pushed into rather than needing the JSON bundled into the Lambda."""
    if os.environ.get("STORAGE_BACKEND", "local").lower() == "aws":
        return _load_from_dynamodb()
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


DEFAULT_PROJECT_BANK_TABLE = "job-automation-project-bank-prod"


def _load_from_dynamodb() -> list[dict]:
    # Defaults to the actual deployed table name rather than silently
    # returning [] when PROJECT_BANK_TABLE isn't set — this env var was
    # added after DYNAMODB_JOBS_TABLE/DYNAMODB_APPLICATIONS_TABLE, so a
    # local .env set up before then (STORAGE_BACKEND=aws to browse real
    # data) wouldn't have it, and jobs/applications would work fine while
    # the project bank silently came back empty — confusing to debug
    # since nothing errors. Verified this was the actual cause 2026-09-09.
    table_name = os.environ.get("PROJECT_BANK_TABLE", DEFAULT_PROJECT_BANK_TABLE)
    import boto3

    region = os.environ.get("AWS_REGION", "us-east-1")
    table = boto3.resource("dynamodb", region_name=region).Table(table_name)
    items: list[dict] = []
    scan_kwargs: dict = {}
    while True:
        response = table.scan(**scan_kwargs)
        items.extend(response.get("Items", []))
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break
        scan_kwargs["ExclusiveStartKey"] = last_key
    return items


def suggest_projects(
    required_skills: list[str],
    count: int = 3,
    bank: Optional[list[dict]] = None,
    seed: Optional[int] = None,
) -> list[dict]:
    """Returns up to `count` project ideas whose tools best overlap with
    required_skills, ranked by overlap size, with a random pick among
    ties (so repeated calls for a popular stack don't always return the
    exact same handful — matches the "randomly pick projects" idea from
    the original request, just scoped to suggestions, not resume writes).
    Returns [] if required_skills is empty or nothing matches."""
    if not required_skills:
        return []
    bank = bank if bank is not None else load_project_bank()
    wanted = {s.lower() for s in required_skills}

    scored: list[tuple[int, dict]] = []
    for project in bank:
        overlap = len({t.lower() for t in project["tools"]} & wanted)
        if overlap > 0:
            scored.append((overlap, project))
    if not scored:
        return []

    max_overlap = max(s for s, _ in scored)
    rng = random.Random(seed)
    results: list[dict] = []
    for overlap_level in range(max_overlap, 0, -1):
        tier = [p for s, p in scored if s == overlap_level]
        rng.shuffle(tier)
        for p in tier:
            if len(results) >= count:
                return results
            results.append(p)
    return results


def list_stacks(bank: Optional[list[dict]] = None) -> list[dict]:
    """Groups the bank by stack for the dashboard's browse page:
    [{"stack": "wordpress", "stack_label": "WordPress", "projects": [...]}, ...],
    sorted by stack_label, projects sorted by title within each group."""
    bank = bank if bank is not None else load_project_bank()
    grouped: dict[str, dict] = {}
    for p in bank:
        key = p.get("stack", "other")
        if key not in grouped:
            grouped[key] = {"stack": key, "stack_label": p.get("stack_label", key), "projects": []}
        grouped[key]["projects"].append(p)
    for group in grouped.values():
        group["projects"].sort(key=lambda p: p["title"])
    return sorted(grouped.values(), key=lambda g: g["stack_label"])


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "project"


def save_project(record: dict, path: Path = PROJECT_BANK_PATH) -> dict:
    """Adds one project to the bank — the local file when
    STORAGE_BACKEND isn't "aws", DynamoDB when it is (same switch as
    load_project_bank). This is a plain data write, not scraping/browser
    automation, so — unlike the Settings page's local-only actions — it's
    allowed on both backends, the same reasoning as "Mark Applied".
    Generates an id from the title if one wasn't supplied. Upserts by id
    either way (overwrites an existing entry with the same id).
    `path` is only used in local mode — mainly a test seam, since Python
    binds default-argument values once at def time, so passing it
    explicitly (rather than monkeypatching PROJECT_BANK_PATH) is what
    actually redirects a call in a test."""
    if not record.get("id"):
        slug = _slugify(record.get("title", "project"))
        fingerprint = hashlib.sha256(json.dumps(record, sort_keys=True).encode("utf-8")).hexdigest()[:6]
        record["id"] = f"custom-{slug}-{fingerprint}"

    if os.environ.get("STORAGE_BACKEND", "local").lower() == "aws":
        _save_to_dynamodb(record)
    else:
        _save_to_local(record, path=path)
    return record


def _save_to_local(record: dict, path: Path = PROJECT_BANK_PATH) -> None:
    records = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    records = [r for r in records if r["id"] != record["id"]]
    records.append(record)
    path.write_text(json.dumps(records, indent=2), encoding="utf-8")


def _save_to_dynamodb(record: dict) -> None:
    table_name = os.environ.get("PROJECT_BANK_TABLE", DEFAULT_PROJECT_BANK_TABLE)
    import boto3

    region = os.environ.get("AWS_REGION", "us-east-1")
    table = boto3.resource("dynamodb", region_name=region).Table(table_name)
    table.put_item(Item=record)


def get_project(project_id: str, bank: Optional[list[dict]] = None) -> Optional[dict]:
    """Fetches one project by id — used to prefill the edit form.
    `bank` lets tests/callers pass a fixed list instead of a fresh load."""
    bank = bank if bank is not None else load_project_bank()
    for p in bank:
        if p["id"] == project_id:
            return p
    return None


def delete_project(project_id: str, path: Path = PROJECT_BANK_PATH) -> bool:
    """Removes one project by id — local file or DynamoDB depending on
    STORAGE_BACKEND, same switch as save_project/load_project_bank.
    Returns True if something was actually removed, False if that id
    didn't exist (so the caller can 404 rather than silently no-op)."""
    if os.environ.get("STORAGE_BACKEND", "local").lower() == "aws":
        return _delete_from_dynamodb(project_id)
    return _delete_from_local(project_id, path=path)


def _delete_from_local(project_id: str, path: Path = PROJECT_BANK_PATH) -> bool:
    if not path.exists():
        return False
    records = json.loads(path.read_text(encoding="utf-8"))
    remaining = [r for r in records if r["id"] != project_id]
    if len(remaining) == len(records):
        return False
    path.write_text(json.dumps(remaining, indent=2), encoding="utf-8")
    return True


def _delete_from_dynamodb(project_id: str) -> bool:
    table_name = os.environ.get("PROJECT_BANK_TABLE", DEFAULT_PROJECT_BANK_TABLE)
    import boto3

    region = os.environ.get("AWS_REGION", "us-east-1")
    table = boto3.resource("dynamodb", region_name=region).Table(table_name)
    response = table.delete_item(Key={"id": project_id}, ReturnValues="ALL_OLD")
    return "Attributes" in response
