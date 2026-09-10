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

from src.common.job_schema import Application, ApplicationStatus, JobSource
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
    # Prefilled on EVERY deploy, including the live AWS one — an explicit,
    # confirmed operator choice made with the tradeoff spelled out: this
    # login page has no auth in front of it, so anyone who has (or later
    # gets) the live dashboard URL can view-source it and read the real
    # DASHBOARD_USERNAME/PASSWORD straight out of the page. This was
    # local-only before; keep that in mind before ever pointing this
    # deploy's URL at anyone else or posting it anywhere public.
    prefill_username = os.environ.get("DASHBOARD_USERNAME", "")
    prefill_password = os.environ.get("DASHBOARD_PASSWORD", "")
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
    from src.common.project_bank import list_stacks

    store = get_store()
    summary = store.get_summary()
    categories = store.get_category_breakdown()
    stack_groups = list_stacks()
    project_bank_stats = {
        "total": sum(len(g["projects"]) for g in stack_groups),
        "stacks": [{"stack_label": g["stack_label"], "count": len(g["projects"])} for g in stack_groups],
    }
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "active": "dashboard",
            "summary": summary,
            "categories": categories,
            "project_bank_stats": project_bank_stats,
            "local_actions_enabled": LOCAL_ACTIONS_ENABLED,
        },
    )


@app.get("/applications", response_class=HTMLResponse)
def applications_page(request: Request):
    store = get_store()
    applications, next_cursor = store.list_applications(limit=DASHBOARD_PAGE_SIZE)
    categories = sorted(store.get_category_breakdown().keys())
    return templates.TemplateResponse(
        request,
        "applications.html",
        {
            "active": "applications",
            "applications": applications,
            "next_cursor": next_cursor,
            "page_size": DASHBOARD_PAGE_SIZE,
            "sources": [s.value for s in JobSource],
            "statuses": [s.value for s in ApplicationStatus],
            "categories": categories,
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
    category: Optional[str] = Query(default=None),
    title: Optional[str] = Query(default=None),
    search: Optional[str] = Query(default=None),
    date_from: Optional[str] = Query(default=None),
    date_to: Optional[str] = Query(default=None),
    limit: int = Query(default=5, le=500),
    cursor: Optional[str] = Query(default=None),
):
    applications, next_cursor = get_store().list_applications(
        status=status, source=source, company=company,
        category=category, title=title, search=search,
        date_from=date_from, date_to=date_to, limit=limit, cursor=cursor,
    )
    return {
        "items": [a.model_dump(mode="json") for a in applications],
        "next_cursor": next_cursor,
    }


@app.get("/api/applications/{job_id}/resume-url")
def api_resume_url(job_id: str):
    return {"job_id": job_id, "resume_url": get_store().get_resume_url(job_id)}


@app.get("/api/project-suggestions")
def project_suggestions(skills: str = Query(default=""), count: int = Query(default=3, le=10)):
    """Suggests project ideas from project_bank.json/DynamoDB matching
    the given skills (comma-separated) — an idea generator for YOU to
    build, never inserted into a resume as claimed work. See
    src/common/project_bank.py's module docstring for why that boundary
    matters."""
    from src.common.project_bank import suggest_projects

    skill_list = [s.strip() for s in skills.split(",") if s.strip()]
    return {"suggestions": suggest_projects(skill_list, count=count)}


@app.post("/api/applications/{job_id}/mark-applied")
def mark_applied(job_id: str):
    """Lets you manually record that you applied to a job yourself (via
    the "Open" link) — NOT an apply bot, which doesn't exist yet. Works
    on both backends; on AWS this is the one write the dashboard's Lambda
    role is granted, scoped to exactly this update (see template.yaml)."""
    if not get_store().mark_applied(job_id):
        raise HTTPException(status_code=404, detail="Application not found")
    return {"job_id": job_id, "status": "applied"}


@app.post("/api/applications/{job_id}/tailor")
def tailor_application(job_id: str):
    """On-demand "Tailor Resume": re-derives the job's required skills
    (whatever the scraper already found + anything a quick best-effort
    live fetch of the posting turns up — see job_fetch.py) and
    regenerates that job's PDF from the operator's REAL resume content
    only — skills reordered, real listed projects reordered by relevance.
    Never inserts a Project Bank idea as if it were a completed project
    (see src/common/project_bank.py's module docstring); use the
    separate "💡 Ideas" button for those. Works on both backends: locally
    off resume/master_resume.json, on AWS off the private S3 copy (see
    src/tailoring/engine.py:_load_master_resume_from_s3)."""
    from src.common.skills import extract_skills
    from src.tailoring.engine import tailor_resume_for_job
    from src.tailoring.job_fetch import fetch_job_text

    store = get_store()
    application = store.get_application(job_id)
    if not application:
        raise HTTPException(status_code=404, detail="Application not found")

    live_text = fetch_job_text(application.url)
    live_skills = extract_skills(live_text) if live_text else []
    merged_skills = list(application.required_skills) + [
        s for s in live_skills if s not in application.required_skills
    ]

    try:
        result = tailor_resume_for_job(job_id, application.title, live_text, merged_skills)
    except FileNotFoundError as e:
        raise HTTPException(status_code=503, detail=str(e))

    tailored_at = store.update_resume_tailoring(
        job_id, result["s3_key"], merged_skills, result.get("resume_filename")
    )
    return {
        "job_id": job_id,
        "tailored_at": tailored_at,
        "skills_used": merged_skills,
        "live_fetch_ok": live_text is not None,
        "llm_tailored": result["llm_tailored"],
    }


@app.get("/api/applications/{job_id}/resume")
def download_resume(job_id: str):
    """Single link that works for either storage backend: redirects to a
    presigned S3 URL when one exists (AWS backend), otherwise serves the
    local PDF straight off disk (local backend / local dev).

    Cache-Control: no-store on the local-file branch, and the dashboard
    template appends a `?v=<resume_tailored_at or updated_at>`
    cache-buster to every link to this route — belt and suspenders
    against a browser showing a PREVIOUSLY-tailored PDF it cached at this
    same URL after "Tailor Resume" regenerates the file (this route
    always serves whatever's on disk right now; nothing here caches
    server-side, so any staleness reported was the browser's, not this
    endpoint's).

    The actual on-disk filename comes from the Application record's
    resume_filename (job-title-timestamp, see
    src/tailoring/engine.py:_resume_filename) — NOT job_id.pdf, which was
    this route's naming convention before that field existed. Still falls
    back to "<job_id>.pdf" for any application tailored before this
    change (resume_filename unset), so an existing local resume/output/
    directory doesn't go dark on upgrade."""
    url = get_store().get_resume_url(job_id)
    if url:
        return RedirectResponse(url, headers={"Cache-Control": "no-store"})
    application = get_store().get_application(job_id)
    filename = (application.resume_filename if application else None) or f"{job_id}.pdf"
    local_path = RESUME_OUTPUT_DIR / filename
    if local_path.exists():
        return FileResponse(
            local_path, media_type="application/pdf", filename=filename,
            headers={"Cache-Control": "no-store"},
        )
    raise HTTPException(status_code=404, detail="Tailored resume not available for this job yet")


# --- Resume library (works on both backends) ------------------------------

@app.get("/resumes", response_class=HTMLResponse)
def resumes_page(request: Request):
    """Every application that has had a resume tailored for it (either at
    scrape time or via an on-demand "Tailor Resume" click), newest first —
    the "see all my resumes" list from direct feedback. Reuses the same
    Application records/download route as the Applications page rather
    than reading resume/output/ (or S3) directly, so it works identically
    on both storage backends with no extra listing code, and always
    reflects the real job title/company/tailored-date a resume belongs
    to, not just a bare filename."""
    store = get_store()
    # A single bulk fetch, filtered in Python — same "hobby scale, not
    # worth a dedicated paginated query" tradeoff already made by
    # DynamoStore.list_applications/get_summary (see that file's
    # comments); 1000 comfortably covers a personal job search.
    applications, _ = store.list_applications(limit=1000)
    resumes = [a for a in applications if a.resume_filename or a.resume_tailored_at]
    return templates.TemplateResponse(
        request, "resumes.html", {"active": "resumes", "resumes": resumes},
    )


# --- Add Job by URL (works on both backends) ------------------------------

def _job_id_from_url(url: str) -> str:
    """Deterministic id derived from the URL itself, not a random one —
    so pasting the SAME URL again re-tailors/refreshes that same
    Application (save_application upserts by job_id) instead of piling up
    duplicates. Mirrors the real scrapers' own idea of a stable per-
    posting job_id; a hash is the fallback here since an arbitrary URL has
    no natural short id of its own."""
    import hashlib

    return "url-" + hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]


@app.get("/add-job", response_class=HTMLResponse)
def add_job_page(request: Request):
    return templates.TemplateResponse(request, "add_job.html", {"active": "add_job"})


@app.post("/api/jobs")
def add_job_by_url(url: str = Form(...)):
    """"Add Job by URL" — direct feedback: "adding this url will create
    [an] entry in job application[s] ... when I click add[,] job
    descrip[tion] is retrieved and resume is tailored and provide[d] to
    user right away". Fetches the posting's text and a best-effort title/
    company guess (src/tailoring/job_fetch.py), derives required_skills
    from it the same way src/pipeline/ingest.py does for a scraped job,
    tailors a resume immediately, saves the Application (so it shows up
    on /applications and /resumes exactly like a scraped one — just with
    source=MANUAL), and hands back a resume download link + the original
    URL as an "Apply Now" link. Works on both storage backends, same as
    the existing on-demand "Tailor Resume" action (see
    tailor_application's docstring above) — not gated to
    LOCAL_ACTIONS_ENABLED, since fetching one already-known URL a human
    just pasted carries the same low ban-risk reasoning fetch_job_text's
    module docstring already lays out for the "Tailor Resume" click."""
    from src.common.skills import extract_skills
    from src.tailoring.engine import tailor_resume_for_job
    from src.tailoring.job_fetch import fetch_job_posting

    url = url.strip()
    if not url:
        raise HTTPException(status_code=422, detail="Paste a job posting URL first.")

    posting = fetch_job_posting(url)
    if posting is None:
        raise HTTPException(
            status_code=422,
            detail="Couldn't fetch that page — check the URL, or the site may be blocking automated requests.",
        )

    # title_guess/company_guess are best-effort (see fetch_job_posting's
    # docstring) - "Untitled Job Posting" is the one case a guess can
    # come back empty (a page with no <title> tag at all), so job_title
    # is never blank going into tailor_resume_for_job (it drives both the
    # resume's header-band role line and its filename).
    title = posting.title_guess or "Untitled Job Posting"
    company = posting.company_guess
    required_skills = extract_skills(posting.text)
    job_id = _job_id_from_url(url)

    try:
        result = tailor_resume_for_job(job_id, title, posting.text, required_skills)
    except FileNotFoundError as e:
        raise HTTPException(status_code=503, detail=str(e))

    application = Application(
        job_id=job_id, source=JobSource.MANUAL, title=title, company=company, url=url,
        status=ApplicationStatus.PENDING, resume_s3_key=result["s3_key"],
        resume_filename=result.get("resume_filename"), required_skills=required_skills,
    )
    get_store().save_application(application)

    return {
        "job_id": job_id,
        "title": title,
        "company": company,
        "url": url,
        "skills_used": required_skills,
        "llm_tailored": result["llm_tailored"],
        "resume_url": f"/api/applications/{job_id}/resume",
    }


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
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "active": "settings",
            "local_actions_enabled": LOCAL_ACTIONS_ENABLED,
            "apply_settings": apply_settings,
        },
    )


@app.get("/scraping", response_class=HTMLResponse)
def scraping_page(request: Request):
    searches = _load_searches() if LOCAL_ACTIONS_ENABLED else []
    return templates.TemplateResponse(
        request,
        "scraping.html",
        {
            "active": "scraping",
            "local_actions_enabled": LOCAL_ACTIONS_ENABLED,
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
    return RedirectResponse("/scraping", status_code=303)


# --- Project bank (works on both backends — see save_project's docstring) --

def _split_lines(value: str) -> list[str]:
    """Form fields for tools/features accept comma- or newline-separated
    free text; this normalizes either into a clean list."""
    parts = value.replace(",", "\n").split("\n")
    return [p.strip() for p in parts if p.strip()]


def _project_record_from_form(
    title: str, stack: str, stack_label: str, category: str,
    tools: str, description: str, features: str, estimated_effort: str,
) -> dict:
    return {
        "stack": stack.strip().lower().replace(" ", "_"),
        "stack_label": stack_label.strip(),
        "category": category.strip() or "general",
        "tools": _split_lines(tools),
        "title": title.strip(),
        "description": description.strip(),
        "features": _split_lines(features),
        "estimated_effort": estimated_effort.strip() or "unknown",
    }


@app.get("/projects", response_class=HTMLResponse)
def projects_page(request: Request):
    from src.common.project_bank import list_stacks

    return templates.TemplateResponse(
        request, "projects.html", {"active": "projects", "stack_groups": list_stacks()},
    )


@app.get("/projects/{project_id}/edit", response_class=HTMLResponse)
def edit_project_form(request: Request, project_id: str):
    from src.common.project_bank import get_project

    project = get_project(project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return templates.TemplateResponse(
        request, "project_edit.html",
        {
            "active": "projects",
            "project": project,
            "tools_text": ", ".join(project["tools"]),
            "features_text": "\n".join(project["features"]),
        },
    )


@app.post("/api/project-bank")
def add_project(
    title: str = Form(...), stack: str = Form(...), stack_label: str = Form(...),
    category: str = Form(""), tools: str = Form(...), description: str = Form(...),
    features: str = Form(...), estimated_effort: str = Form(""),
):
    """Manually adds one project to the bank — a plain data write, not
    scraping/automation, so allowed on both backends unlike Settings."""
    from src.common.project_bank import save_project

    record = _project_record_from_form(title, stack, stack_label, category, tools, description, features, estimated_effort)
    save_project(record)
    return RedirectResponse("/projects", status_code=303)


@app.post("/api/project-bank/{project_id}")
def update_project(
    project_id: str,
    title: str = Form(...), stack: str = Form(...), stack_label: str = Form(...),
    category: str = Form(""), tools: str = Form(...), description: str = Form(...),
    features: str = Form(...), estimated_effort: str = Form(""),
):
    """Edits an existing project — save_project upserts by id, so passing
    the existing id back through overwrites it in place rather than
    creating a duplicate."""
    from src.common.project_bank import save_project

    record = _project_record_from_form(title, stack, stack_label, category, tools, description, features, estimated_effort)
    record["id"] = project_id
    save_project(record)
    return RedirectResponse("/projects", status_code=303)


@app.post("/api/project-bank/{project_id}/delete")
def delete_project_route(project_id: str):
    from src.common.project_bank import delete_project

    if not delete_project(project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    return RedirectResponse("/projects", status_code=303)


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
