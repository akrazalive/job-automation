"""Admin dashboard — reporting + local-only controls.

Local dev:   uvicorn src.dashboard.app:app --reload
AWS:         deployed by infra/aws/template.yaml as a Lambda behind an
             HTTP API (see `handler` at the bottom of this file).

Two different "modes" share this one codebase, gated by LOCAL_ACTIONS_ENABLED
(auto-detected: true unless running inside Lambda):

  - Running locally: full dashboard, PLUS a "Scrape now" trigger and an
    editable Settings page — safe here because scraping/applying happens
    on your own home IP (see TECHNICAL_PLAN.txt section 6).
  - Running on AWS (Lambda): read-only reporting only. Scrape/Settings
    actions are refused with an explanation rather than silently doing
    nothing or (worse) actually running a scraper from a datacenter IP,
    which is exactly the ban risk this architecture was built to avoid.

Every route except /login and /healthz requires a signed session cookie
— see the auth section below. Nothing here holds LinkedIn/Indeed
credentials; this app only reads/displays what the local pipeline wrote.
"""

from __future__ import annotations

import hmac
import os
import threading
from pathlib import Path
from typing import Optional

import yaml
from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from src.common.job_schema import ApplicationStatus, JobSource
from src.storage import get_store

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

app = FastAPI(title="Job Automation — Admin Dashboard")

# True unless actually running inside AWS Lambda (which sets this env var
# automatically — no extra config needed). See module docstring.
LOCAL_ACTIONS_ENABLED = "AWS_LAMBDA_FUNCTION_NAME" not in os.environ

APPLY_SETTINGS_PATH = Path("config/apply_settings.yaml")
SEARCH_CRITERIA_PATH = Path("config/search_criteria.yaml")
RESUME_OUTPUT_DIR = Path("resume/output")

DEFAULT_APPLY_SETTINGS = {
    "apply_delay": {"min_minutes": 3, "max_minutes": 12},
    "daily_application_cap": {"linkedin": 15, "indeed": 15, "simplyhired": 15},
    "scrape_schedule": {"cron": "0 */4 * * *", "description": "Every 4 hours"},
}

_scrape_lock = threading.Lock()


# --- Auth --------------------------------------------------------------
# Simple single-admin session-cookie auth, appropriate for a personal
# tool with one operator — not a multi-user system. Credentials and the
# cookie-signing secret come from env vars (see .env.example /
# infra/aws/template.yaml's NoEcho parameters); change the defaults
# before any real deploy.

SESSION_COOKIE = "job_automation_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 7  # 7 days


def _serializer() -> URLSafeTimedSerializer:
    secret = os.environ.get("SESSION_SECRET", "dev-insecure-secret-change-me")
    return URLSafeTimedSerializer(secret, salt="job-automation-dashboard")


def _check_credentials(username: str, password: str) -> bool:
    expected_user = os.environ.get("DASHBOARD_USERNAME", "admin")
    expected_pass = os.environ.get("DASHBOARD_PASSWORD", "change-me-now")
    return hmac.compare_digest(username, expected_user) and hmac.compare_digest(password, expected_pass)


def _is_authenticated(request: Request) -> bool:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return False
    try:
        _serializer().loads(token, max_age=SESSION_MAX_AGE)
        return True
    except (BadSignature, SignatureExpired):
        return False


@app.middleware("http")
async def auth_gate(request: Request, call_next):
    open_paths = {"/login", "/healthz"}
    if request.url.path in open_paths or _is_authenticated(request):
        return await call_next(request)
    if request.url.path.startswith("/api/") or request.url.path.startswith("/actions/"):
        return JSONResponse({"detail": "Not authenticated"}, status_code=401)
    return RedirectResponse("/login")


@app.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    # Prefill only when running locally. NEVER on the AWS deploy — that
    # page is reachable by anyone with the URL with no auth at all, so
    # baking the real password into its HTML would defeat the login
    # entirely (view-source gets it). Locally, only you can reach
    # 127.0.0.1, so the convenience tradeoff is reasonable there.
    prefill_username = os.environ.get("DASHBOARD_USERNAME", "") if LOCAL_ACTIONS_ENABLED else ""
    prefill_password = os.environ.get("DASHBOARD_PASSWORD", "") if LOCAL_ACTIONS_ENABLED else ""
    return templates.TemplateResponse(
        request,
        "login.html",
        {"error": None, "prefill_username": prefill_username, "prefill_password": prefill_password},
    )


@app.post("/login")
def login_submit(request: Request, username: str = Form(...), password: str = Form(...)):
    if not _check_credentials(username, password):
        return templates.TemplateResponse(
            request, "login.html", {"error": "Invalid username or password"}, status_code=401
        )
    token = _serializer().dumps({"u": username})
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(
        SESSION_COOKIE, token, httponly=True, samesite="lax",
        secure=(request.url.scheme == "https"), max_age=SESSION_MAX_AGE,
    )
    return resp


@app.get("/logout")
def logout():
    resp = RedirectResponse("/login")
    resp.delete_cookie(SESSION_COOKIE)
    return resp


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


# --- Dashboard -----------------------------------------------------------

DASHBOARD_PAGE_SIZE = 5  # UI also offers 5/10/15/20 via a "rows per page" selector


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    store = get_store()
    summary = store.get_summary()
    applications, next_cursor = store.list_applications(limit=DASHBOARD_PAGE_SIZE)
    categories = store.get_category_breakdown()
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "summary": summary,
            "applications": applications,
            "next_cursor": next_cursor,
            "page_size": DASHBOARD_PAGE_SIZE,
            "categories": categories,
            "sources": [s.value for s in JobSource],
            "statuses": [s.value for s in ApplicationStatus],
            "local_actions_enabled": LOCAL_ACTIONS_ENABLED,
        },
    )


@app.get("/api/summary")
def api_summary():
    return get_store().get_summary()


@app.get("/api/categories")
def api_categories():
    return get_store().get_category_breakdown()


@app.get("/api/applications")
def api_applications(
    status: Optional[str] = Query(default=None),
    source: Optional[str] = Query(default=None),
    company: Optional[str] = Query(default=None),
    date_from: Optional[str] = Query(default=None),
    date_to: Optional[str] = Query(default=None),
    limit: int = Query(default=5, le=500),
    cursor: Optional[str] = Query(default=None),
):
    applications, next_cursor = get_store().list_applications(
        status=status, source=source, company=company,
        date_from=date_from, date_to=date_to, limit=limit, cursor=cursor,
    )
    return {
        "items": [a.model_dump(mode="json") for a in applications],
        "next_cursor": next_cursor,
    }


@app.get("/api/applications/{job_id}/resume-url")
def api_resume_url(job_id: str):
    return {"job_id": job_id, "resume_url": get_store().get_resume_url(job_id)}


@app.post("/api/applications/{job_id}/mark-applied")
def mark_applied(job_id: str):
    """Lets you manually record that you applied to a job yourself (via
    the "Open" link) — NOT an apply bot, which doesn't exist yet. Works
    on both backends; on AWS this is the one write the dashboard's Lambda
    role is granted, scoped to exactly this update (see template.yaml)."""
    if not get_store().mark_applied(job_id):
        raise HTTPException(status_code=404, detail="Application not found")
    return {"job_id": job_id, "status": "applied"}


@app.get("/api/applications/{job_id}/resume")
def download_resume(job_id: str):
    """Single link that works for either storage backend: redirects to a
    presigned S3 URL when one exists (AWS backend), otherwise serves the
    local PDF straight off disk (local backend / local dev)."""
    url = get_store().get_resume_url(job_id)
    if url:
        return RedirectResponse(url)
    local_path = RESUME_OUTPUT_DIR / f"{job_id}.pdf"
    if local_path.exists():
        return FileResponse(local_path, media_type="application/pdf", filename=f"{job_id}.pdf")
    raise HTTPException(status_code=404, detail="Tailored resume not available for this job yet")


# --- Settings (local runs only) ------------------------------------------

def _load_apply_settings() -> dict:
    if APPLY_SETTINGS_PATH.exists():
        loaded = yaml.safe_load(APPLY_SETTINGS_PATH.read_text(encoding="utf-8"))
        return loaded or DEFAULT_APPLY_SETTINGS
    return DEFAULT_APPLY_SETTINGS


def _load_searches() -> list[dict]:
    if SEARCH_CRITERIA_PATH.exists():
        loaded = yaml.safe_load(SEARCH_CRITERIA_PATH.read_text(encoding="utf-8"))
        return (loaded or {}).get("searches", [])
    return []


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request):
    apply_settings = _load_apply_settings() if LOCAL_ACTIONS_ENABLED else DEFAULT_APPLY_SETTINGS
    searches = _load_searches() if LOCAL_ACTIONS_ENABLED else []
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "local_actions_enabled": LOCAL_ACTIONS_ENABLED,
            "apply_settings": apply_settings,
            "searches": searches,
        },
    )


@app.post("/settings/apply-delay")
def save_apply_delay(min_minutes: int = Form(...), max_minutes: int = Form(...)):
    if not LOCAL_ACTIONS_ENABLED:
        raise HTTPException(status_code=403, detail="Settings are edited locally — see README")
    settings = _load_apply_settings()
    settings["apply_delay"] = {"min_minutes": min_minutes, "max_minutes": max_minutes}
    APPLY_SETTINGS_PATH.write_text(yaml.safe_dump(settings, sort_keys=False), encoding="utf-8")
    return RedirectResponse("/settings", status_code=303)


@app.post("/settings/schedule")
def save_schedule(cron: str = Form(...), description: str = Form("")):
    if not LOCAL_ACTIONS_ENABLED:
        raise HTTPException(status_code=403, detail="Settings are edited locally — see README")
    settings = _load_apply_settings()
    settings["scrape_schedule"] = {"cron": cron, "description": description}
    APPLY_SETTINGS_PATH.write_text(yaml.safe_dump(settings, sort_keys=False), encoding="utf-8")
    return RedirectResponse("/settings", status_code=303)


@app.post("/settings/add-search")
def add_search(query: str = Form(...), location: str = Form("Remote"), category: str = Form("")):
    if not LOCAL_ACTIONS_ENABLED:
        raise HTTPException(status_code=403, detail="Settings are edited locally — see README")
    config = yaml.safe_load(SEARCH_CRITERIA_PATH.read_text(encoding="utf-8")) if SEARCH_CRITERIA_PATH.exists() else {}
    config.setdefault("searches", []).append(
        {"query": query, "location": location, "category": category or None}
    )
    SEARCH_CRITERIA_PATH.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return RedirectResponse("/settings", status_code=303)


# --- Actions (local runs only) --------------------------------------------

@app.post("/actions/scrape")
def trigger_scrape():
    if not LOCAL_ACTIONS_ENABLED:
        raise HTTPException(
            status_code=403,
            detail="Scraping runs locally only (avoids applying from a datacenter IP — "
            "see TECHNICAL_PLAN.txt section 6). Run `python -m src.pipeline.ingest` on "
            "your own machine instead.",
        )
    if not _scrape_lock.acquire(blocking=False):
        return {"started": False, "reason": "A scrape is already running"}

    def _run():
        try:
            from src.pipeline import ingest as ingest_pipeline  # local-only dep (playwright)

            ingest_pipeline.run()
        finally:
            _scrape_lock.release()

    threading.Thread(target=_run, daemon=True).start()
    return {"started": True}


@app.get("/actions/scrape-status")
def scrape_status():
    if not LOCAL_ACTIONS_ENABLED:
        return {"running": False, "disabled": True}
    from src.pipeline import status as pipeline_status  # local-only

    result = pipeline_status.read_status()
    result["log"] = pipeline_status.read_log(tail=60)
    return result


# --- AWS Lambda entrypoint -------------------------------------------------
try:
    from mangum import Mangum

    handler = Mangum(app)
except ImportError:  # pragma: no cover
    handler = None
