"""Loads project_bank.json (repo root) into the live ProjectBankTable.

Run locally after `python scripts/generate_project_bank.py` (or any edit
to project_bank.json) and after infra/aws has been deployed at least
once (the table must already exist):

    $env:AWS_REGION="us-east-1"
    $env:PROJECT_BANK_TABLE="job-automation-project-bank-prod"
    python scripts/load_project_bank_to_dynamodb.py

Default mode is UPSERT-ONLY (put_item per record from project_bank.json;
nothing is deleted). This is deliberate: projects can now also be added
directly to the live table via the dashboard's "Add project" form
(src/dashboard/app.py -> src/common/project_bank.py:save_project), which
never touches the local project_bank.json file — a destructive
delete-then-write sync would silently wipe those out. Pass --replace-all
if you specifically want the old destructive behavior (e.g. cleaning up
after removing/renaming entries in the local file) — it deletes every
item not present in project_bank.json, so use it deliberately, not as
the default habit.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import boto3

PROJECT_BANK_PATH = Path(__file__).resolve().parent.parent / "project_bank.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--replace-all", action="store_true",
        help="Delete every item in the table not present in project_bank.json "
             "(destructive to entries added directly via the dashboard on AWS "
             "that never made it into the local file). Default is upsert-only.",
    )
    args = parser.parse_args()

    table_name = os.environ.get("PROJECT_BANK_TABLE")
    if not table_name:
        raise SystemExit("Set PROJECT_BANK_TABLE first (see this script's docstring).")
    region = os.environ.get("AWS_REGION", "us-east-1")

    records = json.loads(PROJECT_BANK_PATH.read_text(encoding="utf-8"))
    print(f"Loaded {len(records)} records from {PROJECT_BANK_PATH}")

    table = boto3.resource("dynamodb", region_name=region).Table(table_name)

    if args.replace_all:
        existing_ids = []
        scan_kwargs: dict = {"ProjectionExpression": "id"}
        while True:
            response = table.scan(**scan_kwargs)
            existing_ids.extend(item["id"] for item in response.get("Items", []))
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                break
            scan_kwargs["ExclusiveStartKey"] = last_key

        local_ids = {r["id"] for r in records}
        to_delete = [i for i in existing_ids if i not in local_ids]
        if to_delete:
            print(f"--replace-all: deleting {len(to_delete)} items not in the local file...")
            with table.batch_writer() as batch:
                for item_id in to_delete:
                    batch.delete_item(Key={"id": item_id})

    print(f"Upserting {len(records)} items...")
    with table.batch_writer() as batch:
        for record in records:
            batch.put_item(Item=record)

    print(f"Done. {table_name} synced from {PROJECT_BANK_PATH}.")


if __name__ == "__main__":
    main()
