"""Production ApplicationStore — DynamoDB + S3, AWS Free Tier.

Table/bucket names and region come from environment variables that
infra/aws/template.yaml (AWS SAM) sets automatically on the deployed
Lambda; for local testing against real AWS resources, set the same
variables in .env (see .env.example) and STORAGE_BACKEND=aws.
"""

from __future__ import annotations

import os
from typing import Optional

import boto3
from boto3.dynamodb.conditions import Attr

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
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> tuple[list[Application], Optional[str]]:
        # NOTE: this scans the table, which is fine at hobby scale (a few
        # hundred/thousand applications). If volume grows enough that scans
        # get slow/costly, add a GSI on status (and/or updated_at) and
        # switch this to a Query — tracked in TECHNICAL_PLAN.txt Phase 7.
        scan_kwargs: dict = {}
        filters = []
        if status:
            filters.append(Attr("status").eq(status))
        if source:
            filters.append(Attr("source").eq(source))
        if company:
            filters.append(Attr("company").contains(company))
        if date_from:
            filters.append(Attr("updated_at").gte(date_from))
        if date_to:
            filters.append(Attr("updated_at").lte(date_to))
        if filters:
            expr = filters[0]
            for f in filters[1:]:
                expr = expr & f
            scan_kwargs["FilterExpression"] = expr
        if cursor:
            scan_kwargs["ExclusiveStartKey"] = {"job_id": cursor}

        response = self.applications_table.scan(**scan_kwargs)
        items = response.get("Items", [])
        items.sort(key=lambda r: r.get("updated_at", ""), reverse=True)
        page = items[:limit]
        next_cursor = response.get("LastEvaluatedKey", {}).get("job_id")
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
