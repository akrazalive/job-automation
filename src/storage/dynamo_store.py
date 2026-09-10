"""Production ApplicationStore — DynamoDB + S3, AWS Free Tier.

Table/bucket names and region come from environment variables that
infra/aws/template.yaml (AWS SAM) sets automatically on the deployed
Lambda; for local testing against real AWS resources, set the same
variables in .env (see .env.example) and STORAGE_BACKEND=aws.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Optional

import boto3
from botocore.exceptions import ClientError

from src.common.job_schema import Application, ApplicationStatus, Job
from src.storage.base import ApplicationStore


class DynamoStore(ApplicationStore):
    def __init__(self):
        region = os.environ.get("AWS_REGION", "us-east-1")
        self._dynamodb = boto3.resource("dynamodb", region_name=region)
        self._s3 = boto3.client("s3", region_name=region)
        self.applications_table = self._dynamodb.Table(
            os.environ["DYNAMODB_APPLICATIONS_TABLE"]
        )
        self.jobs_table = self._dynamodb.Table(os.environ["DYNAMODB_JOBS_TABLE"])
        self.bucket = os.environ.get("S3_BUCKET_NAME")

    def list_applications(
        self,
        status: Optional[str] = None,
        source: Optional[str] = None,
        company: Optional[str] = None,
        category: Optional[str] = None,
        title: Optional[str] = None,
        search: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> tuple[list[Application], Optional[str]]:
        # NOTE: fetches the whole table into memory every call, then
        # filters in Python (same logic as LocalJsonStore, deliberately
        # kept identical rather than half in a DynamoDB FilterExpression
        # and half in Python — a Scan's RCU cost is driven by items
        # *read*, not items *returned*, so pushing filters into
        # FilterExpression saves network bytes but not read cost; at
        # hobby scale (a few hundred/thousand applications) that's not
        # worth the duplicated, harder-to-keep-in-sync filtering logic,
        # and Python-side filtering gets free case-insensitive substring
        # matching that DynamoDB's own `contains` doesn't do natively.
        # If volume grows enough that this gets slow/costly, add a GSI on
        # status (and/or updated_at) and switch to a genuinely paginated
        # Query — tracked in TECHNICAL_PLAN.txt Phase 7.
        #
        # Cursor is an OFFSET into the filtered+sorted list (same scheme
        # as LocalJsonStore), not DynamoDB's own LastEvaluatedKey —
        # deliberate: LastEvaluatedKey only appears once a single Scan
        # response exceeds DynamoDB's 1MB cap, which small tables never
        # hit, so using it as "is there a next page" silently broke
        # pagination for any table under ~1MB (verified 2026-09-08: 84
        # items, 25-per-page UI, "Next" stayed disabled the whole time).

        # Loop DynamoDB's OWN internal pagination (its 1MB-per-response
        # cap) until exhausted, so results are never silently incomplete
        # once the table does grow past that — separate from our offset
        # cursor below, which paginates what the UI displays.
        items: list[dict] = []
        scan_kwargs: dict = {}
        while True:
            response = self.applications_table.scan(**scan_kwargs)
            items.extend(response.get("Items", []))
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                break
            scan_kwargs["ExclusiveStartKey"] = last_key

        if status:
            items = [i for i in items if i.get("status") == status]
        if source:
            items = [i for i in items if i.get("source") == source]
        if company:
            items = [i for i in items if company.lower() in (i.get("company") or "").lower()]
        if category:
            items = [i for i in items if (i.get("category") or "").lower() == category.lower()]
        if title:
            items = [i for i in items if title.lower() in (i.get("title") or "").lower()]
        if search:
            needle = search.lower()
            items = [
                i for i in items
                if needle in (i.get("title") or "").lower()
                or needle in (i.get("company") or "").lower()
                or any(needle in s.lower() for s in i.get("required_skills", []))
            ]
        if date_from:
            items = [i for i in items if (i.get("applied_at") or i.get("updated_at", "")) >= date_from]
        if date_to:
            items = [i for i in items if (i.get("applied_at") or i.get("updated_at", "")) <= date_to]

        items.sort(key=lambda r: r.get("updated_at", ""), reverse=True)

        offset = int(cursor) if cursor else 0
        page = items[offset : offset + limit]
        next_cursor = str(offset + limit) if offset + limit < len(items) else None
        return [Application(**i) for i in page], next_cursor

    def get_summary(self) -> dict:
        response = self.applications_table.scan(
            ProjectionExpression="#s", ExpressionAttributeNames={"#s": "status"}
        )
        items = response.get("Items", [])
        summary = {s.value: 0 for s in ApplicationStatus}
        for i in items:
            summary[i["status"]] = summary.get(i["status"], 0) + 1
        summary["total"] = len(items)
        return summary

    def get_job(self, job_id: str) -> Optional[Job]:
        response = self.jobs_table.get_item(Key={"job_id": job_id})
        item = response.get("Item")
        return Job(**item) if item else None

    def get_resume_url(self, job_id: str) -> Optional[str]:
        response = self.applications_table.get_item(Key={"job_id": job_id})
        item = response.get("Item")
        key = item.get("resume_s3_key") if item else None
        if not key or not self.bucket:
            return None
        return self._s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=300,
        )

    def save_application(self, application: Application) -> None:
        import json

        item = json.loads(application.model_dump_json())
        self.applications_table.put_item(Item=item)

    def get_category_breakdown(self) -> dict:
        # Same hobby-scale Scan caveat as list_applications/get_summary.
        response = self.applications_table.scan(
            ProjectionExpression="category, #s",
            ExpressionAttributeNames={"#s": "status"},
        )
        items = response.get("Items", [])
        breakdown: dict = {}
        for i in items:
            cat = i.get("category") or "Uncategorized"
            bucket = breakdown.setdefault(cat, {"total": 0})
            bucket["total"] += 1
            bucket[i["status"]] = bucket.get(i["status"], 0) + 1
        return breakdown

    def mark_applied(self, job_id: str) -> bool:
        # The only write the dashboard's own Lambda role is granted (see
        # infra/aws/template.yaml's DashboardFunction Policies) - scoped
        # to exactly this UpdateExpression on the Applications table,
        # deliberately narrower than a general CRUD policy. Everything
        # else the dashboard does stays read-only.
        now = datetime.now(timezone.utc).isoformat()
        try:
            self.applications_table.update_item(
                Key={"job_id": job_id},
                UpdateExpression="SET #s = :applied, applied_at = :now, updated_at = :now",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={":applied": "applied", ":now": now},
                ConditionExpression="attribute_exists(job_id)",
            )
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise

    def get_application(self, job_id: str) -> Optional[Application]:
        response = self.applications_table.get_item(Key={"job_id": job_id})
        item = response.get("Item")
        return Application(**item) if item else None

    def update_resume_tailoring(
        self,
        job_id: str,
        resume_s3_key: Optional[str],
        tailored_skills: list[str],
        resume_filename: Optional[str] = None,
    ) -> Optional[str]:
        # Same UpdateItem permission as mark_applied covers this (see its
        # comment above) - not attribute-restricted in IAM.
        now = datetime.now(timezone.utc).isoformat()
        update_expr = "SET resume_tailored_at = :now, resume_tailored_skills = :skills, updated_at = :now"
        expr_values: dict = {":now": now, ":skills": tailored_skills}
        if resume_s3_key:
            update_expr += ", resume_s3_key = :key"
            expr_values[":key"] = resume_s3_key
        if resume_filename:
            update_expr += ", resume_filename = :fname"
            expr_values[":fname"] = resume_filename
        try:
            self.applications_table.update_item(
                Key={"job_id": job_id},
                UpdateExpression=update_expr,
                ExpressionAttributeValues=expr_values,
                ConditionExpression="attribute_exists(job_id)",
            )
            return now
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return None
            raise
