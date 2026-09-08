"""Admin dashboard — read-only view over the application log.

Local dev:   uvicorn src.dashboard.app:app --reload
AWS:         deployed by infra/aws/template.yaml as a Lambda behind an
             HTTP API (see `handler` at the bottom of this file).

This app never scrapes or applies to jobs itself — it only reads what the
(locally-run) scraper/apply pipeline has written to the ApplicationStore
(see src/storage/). Read-only by design: it holds no site credentials.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from src.common.job_schema import ApplicationStatus, JobSource
from src.storage import get_store

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

app = FastAPI(title="Job Automation — Admin Dashboard")


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    store = get_store()
    summary = store.get_summary()
    applications, _ = store.list_applications(limit=100)
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "summary": summary,
            "applications": applications,
            "sources": [s.value for s in JobSource],
            "statuses": [s.value for s in ApplicationStatus],
        },
    )


@app.get("/api/summary")
def api_summary():
    return get_store().get_summary()


@app.get("/api/applications")
def api_applications(
    status: Optional[str] = Query(default=None),
    source: Optional[str] = Query(default=None),
    company: Optional[str] = Query(default=None),
    date_from: Optional[str] = Query(default=None),
    date_to: Optional[str] = Query(default=None),
    limit: int = Query(default=100, le=500),
    cursor: Optional[str] = Query(default=None),
):
    applications, next_cursor = get_store().list_applications(
        status=status,
        source=source,
        company=company,
        date_from=date_from,
        date_to=date_to,
        limit=limit,
        cursor=cursor,
    )
    return {
        "items": [a.model_dump(mode="json") for a in applications],
        "next_cursor": next_cursor,
    }


@app.get("/api/applications/{job_id}/resume-url")
def api_resume_url(job_id: str):
    return {"job_id": job_id, "resume_url": get_store().get_resume_url(job_id)}


# --- AWS Lambda entrypoint -------------------------------------------------
# Only imported/used when deployed (see infra/aws/template.yaml, Handler:
# src.dashboard.app.handler). Kept optional so `pip install -r
# requirements.txt` without mangum still lets the app run locally.
try:
    from mangum import Mangum

    handler = Mangum(app)
except ImportError:  # pragma: no cover
    handler = None
