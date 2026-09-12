import os

os.environ.setdefault("STORAGE_BACKEND", "local")
os.environ.setdefault("DASHBOARD_USERNAME", "admin")
os.environ.setdefault("DASHBOARD_PASSWORD", "test-password-123")
os.environ.setdefault("SESSION_SECRET", "test-secret")

from fastapi.testclient import TestClient

from src.dashboard.app import app

client = TestClient(app)


def _login() -> TestClient:
    c = TestClient(app)
    resp = c.post("/login", data={"username": "admin", "password": "test-password-123"}, follow_redirects=False)
    assert resp.status_code == 303
    return c


def test_healthz_does_not_require_auth():
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_dashboard_redirects_when_not_logged_in():
    resp = client.get("/", follow_redirects=False)
    assert resp.status_code in (302, 303, 307)
    assert resp.headers["location"] == "/login"


def test_api_requires_auth_returns_401_not_redirect():
    resp = client.get("/api/summary")
    assert resp.status_code == 401


def test_login_wrong_password_rejected():
    resp = client.post("/login", data={"username": "admin", "password": "wrong"})
    assert resp.status_code == 401
    assert "Invalid" in resp.text


def test_login_then_dashboard_page_renders():
    c = _login()
    resp = c.get("/")
    assert resp.status_code == 200
    assert "<h1>Dashboard</h1>" in resp.text
    assert "Project bank by stack" in resp.text  # stats section
    assert 'id="app-table"' not in resp.text  # jobs table lives on /applications now


def test_login_then_applications_page_renders():
    c = _login()
    resp = c.get("/applications")
    assert resp.status_code == 200
    assert "<h1>Applications</h1>" in resp.text
    assert 'id="app-table"' in resp.text


def test_resumes_page_renders_and_is_in_the_nav():
    # Direct feedback: "I need a menu item going to which I can see all
    # my resumees".
    c = _login()
    resp = c.get("/resumes")
    assert resp.status_code == 200
    assert "<h1>My Resumes</h1>" in resp.text
    assert 'href="/resumes"' in resp.text  # the sidebar nav link itself


def test_applications_page_has_search_category_and_title_filters():
    c = _login()
    resp = c.get("/applications")
    assert resp.status_code == 200
    assert 'id="f-search"' in resp.text
    assert 'id="f-category"' in resp.text
    assert 'id="f-title"' in resp.text


def test_api_applications_filters_by_category_and_title():
    c = _login()
    resp = c.get("/api/applications", params={"category": "fullstack", "limit": 50})
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert all(item["category"] == "fullstack" for item in items)

    resp2 = c.get("/api/applications", params={"title": "full stack", "limit": 50})
    assert resp2.status_code == 200
    assert all("full stack" in item["title"].lower() for item in resp2.json()["items"])


def test_api_applications_search_matches_across_fields():
    c = _login()
    resp = c.get("/api/applications", params={"search": "react", "limit": 50})
    assert resp.status_code == 200
    for item in resp.json()["items"]:
        haystack = (item["title"] + item["company"] + " ".join(item["required_skills"])).lower()
        assert "react" in haystack


def test_login_then_api_summary():
    c = _login()
    resp = c.get("/api/summary")
    assert resp.status_code == 200
    assert "total" in resp.json()


def test_login_then_api_applications():
    c = _login()
    resp = c.get("/api/applications")
    assert resp.status_code == 200
    body = resp.json()
    assert "items" in body
    assert isinstance(body["items"], list)


def test_api_applications_total_reflects_filtered_count_not_page_size():
    # total exists for the dashboard's numbered page buttons (needs
    # total pages = ceil(total / limit)) and must count every MATCHING
    # record, not just what fits on one page.
    c = _login()
    resp = c.get("/api/applications", params={"limit": 1})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) <= 1
    assert body["total"] >= len(body["items"])


def test_bulk_delete_endpoint_removes_only_the_given_jobs():
    from datetime import datetime, timezone

    from src.common.job_schema import Application, ApplicationStatus, JobSource
    from src.storage import get_store

    store = get_store()

    def _app(job_id: str) -> Application:
        return Application(
            job_id=job_id, source=JobSource.MANUAL, title="Bulk Delete Test",
            company="Test Co", url=f"https://example.com/{job_id}",
            status=ApplicationStatus.PENDING, updated_at=datetime.now(timezone.utc),
        )

    store.save_application(_app("bulk-delete-test-1"))
    store.save_application(_app("bulk-delete-test-2"))

    c = _login()
    resp = c.post("/api/applications/bulk-delete", json={"job_ids": ["bulk-delete-test-1", "does-not-exist"]})
    assert resp.status_code == 200
    body = resp.json()
    assert body["deleted"] == ["bulk-delete-test-1"]
    assert body["not_found"] == ["does-not-exist"]
    assert store.get_application("bulk-delete-test-1") is None
    assert store.get_application("bulk-delete-test-2") is not None

    store.delete_application("bulk-delete-test-2")  # clean up the second test record too


def test_delete_all_endpoint_calls_the_store_and_returns_its_count(monkeypatch):
    # Deliberately does NOT exercise this against the real shared local
    # store (unlike the bulk-delete test above) - delete_all_applications
    # would wipe every fixture/sample record every OTHER test in this
    # session depends on. The store-level behavior (every record AND its
    # resume file actually gone) is covered in isolation, per tmp_path
    # store, by tests/test_storage_writes.py instead; this just checks
    # the route wires up to it and shapes its response correctly.
    from src.dashboard import app as dashboard_app

    class _FakeStore:
        def delete_all_applications(self) -> int:
            return 42

    monkeypatch.setattr(dashboard_app, "get_store", lambda: _FakeStore())

    c = _login()
    resp = c.post("/api/applications/delete-all")
    assert resp.status_code == 200
    assert resp.json() == {"deleted": 42}


def test_scrape_stop_endpoint_sets_the_stop_flag():
    from src.pipeline import status as pipeline_status

    c = _login()
    resp = c.post("/actions/scrape-stop")
    assert resp.status_code == 200
    assert resp.json() == {"stop_requested": True}
    assert pipeline_status.should_stop() is True
    pipeline_status.clear_stop()  # leave the real status file clean for any other local run


def test_login_then_api_applications_filter_by_status():
    c = _login()
    resp = c.get("/api/applications", params={"status": "applied"})
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert all(item["status"] == "applied" for item in items)


def test_login_then_categories_endpoint():
    c = _login()
    resp = c.get("/api/categories")
    assert resp.status_code == 200
    assert isinstance(resp.json(), dict)


def test_login_then_settings_page_renders():
    c = _login()
    resp = c.get("/settings")
    assert resp.status_code == 200
    assert "Settings" in resp.text
    # Manual scrape / keywords moved to /scraping - shouldn't be here anymore
    assert "Manual scrape" not in resp.text


def test_login_then_scraping_page_renders():
    c = _login()
    resp = c.get("/scraping")
    assert resp.status_code == 200
    assert "Manual scrape" in resp.text
    assert "Search keywords" in resp.text


def test_sidebar_active_state_is_set_per_page():
    c = _login()
    assert 'href="/" class="active"' in c.get("/").text
    assert 'href="/applications" class="active"' in c.get("/applications").text
    assert 'href="/projects" class="active"' in c.get("/projects").text
    assert 'href="/scraping" class="active"' in c.get("/scraping").text
    assert 'href="/settings" class="active"' in c.get("/settings").text


def test_logout_clears_session():
    c = _login()
    assert c.get("/").status_code == 200
    c.get("/logout", follow_redirects=False)
    resp = c.get("/", follow_redirects=False)
    assert resp.status_code in (302, 303, 307)
    assert resp.headers["location"] == "/login"


def test_mark_applied_updates_status():
    c = _login()
    items = c.get("/api/applications", params={"limit": 50}).json()["items"]
    pending = next(i for i in items if i["status"] != "applied")

    resp = c.post(f"/api/applications/{pending['job_id']}/mark-applied")
    assert resp.status_code == 200
    assert resp.json()["status"] == "applied"

    refreshed = c.get("/api/applications", params={"limit": 50}).json()["items"]
    updated = next(i for i in refreshed if i["job_id"] == pending["job_id"])
    assert updated["status"] == "applied"


def test_mark_applied_unknown_job_returns_404():
    c = _login()
    resp = c.post("/api/applications/does-not-exist/mark-applied")
    assert resp.status_code == 404


def test_tailor_resume_endpoint(monkeypatch):
    # Both are imported lazily inside the route (same reason as
    # save_project elsewhere in this file) - patch the source modules'
    # attributes so the request picks up the fakes instead of actually
    # fetching a URL or rendering a PDF.
    monkeypatch.setattr("src.tailoring.job_fetch.fetch_job_text", lambda url, timeout=8.0: "Looking for a React and AWS expert.")
    captured = {}

    fake_filename = "react-and-aws-expert-20260101000000.pdf"

    def fake_tailor(job_id, job_title, job_description, required_skills):
        captured["args"] = (job_id, job_title, job_description, required_skills)
        return {
            "local_path": f"resume/output/{fake_filename}", "s3_key": None,
            "llm_tailored": False, "resume_filename": fake_filename,
        }

    monkeypatch.setattr("src.tailoring.engine.tailor_resume_for_job", fake_tailor)

    c = _login()
    items = c.get("/api/applications", params={"limit": 50}).json()["items"]
    target = items[0]

    resp = c.post(f"/api/applications/{target['job_id']}/tailor")
    assert resp.status_code == 200
    body = resp.json()
    assert body["job_id"] == target["job_id"]
    assert body["tailored_at"] is not None
    assert body["live_fetch_ok"] is True
    assert "React" in body["skills_used"]
    assert "AWS" in body["skills_used"]

    job_id, job_title, job_description, required_skills = captured["args"]
    assert job_id == target["job_id"]
    assert job_description == "Looking for a React and AWS expert."

    refreshed = c.get("/api/applications", params={"limit": 50}).json()["items"]
    updated = next(i for i in refreshed if i["job_id"] == target["job_id"])
    assert updated["resume_tailored_at"] is not None
    # The filename tailor_resume_for_job returns (job-title-timestamp, see
    # src/tailoring/engine.py) must land on the Application record, not
    # just get thrown away — it's what the download route and the "My
    # Resumes" page key off of.
    assert updated["resume_filename"] == fake_filename

    resumes_page = c.get("/resumes")
    assert resumes_page.status_code == 200
    assert fake_filename in resumes_page.text


def test_tailor_resume_unknown_job_returns_404(monkeypatch):
    monkeypatch.setattr("src.tailoring.job_fetch.fetch_job_text", lambda url, timeout=8.0: None)
    c = _login()
    resp = c.post("/api/applications/does-not-exist/tailor")
    assert resp.status_code == 404


def test_add_job_page_renders():
    c = _login()
    resp = c.get("/add-job")
    assert resp.status_code == 200
    assert "<h1>Add Job</h1>" in resp.text


def test_add_job_by_url_creates_application_and_returns_resume_and_apply_links(monkeypatch):
    from src.tailoring.job_fetch import JobPosting

    fake_url = "https://boards.example.com/postings/12345"
    fake_posting = JobPosting(
        text="Looking for a React and AWS expert.",
        title_guess="Senior React Developer",
        company_guess="boards.example.com",
    )
    monkeypatch.setattr("src.tailoring.job_fetch.fetch_job_posting", lambda url, timeout=8.0: fake_posting)

    fake_filename = "senior-react-developer-20260101000000.pdf"

    def fake_tailor(job_id, job_title, job_description, required_skills):
        return {
            "local_path": f"resume/output/{fake_filename}", "s3_key": None,
            "llm_tailored": False, "resume_filename": fake_filename,
        }

    monkeypatch.setattr("src.tailoring.engine.tailor_resume_for_job", fake_tailor)

    c = _login()
    resp = c.post("/api/jobs", data={"url": fake_url})
    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "Senior React Developer"
    assert body["company"] == "boards.example.com"
    assert body["url"] == fake_url
    assert "React" in body["skills_used"]
    assert "AWS" in body["skills_used"]
    assert body["resume_url"] == f"/api/applications/{body['job_id']}/resume"

    # It really did land as a new Application - visible on both the
    # Applications list and the "My Resumes" list, source=manual.
    apps = c.get("/api/applications", params={"limit": 200}).json()["items"]
    created = next(a for a in apps if a["job_id"] == body["job_id"])
    assert created["source"] == "manual"
    assert created["url"] == fake_url
    assert created["resume_filename"] == fake_filename

    resumes_page = c.get("/resumes")
    assert fake_filename in resumes_page.text


def test_add_job_by_url_rejects_blank_url():
    c = _login()
    resp = c.post("/api/jobs", data={"url": "   "})
    assert resp.status_code == 422


def test_add_job_by_url_returns_422_when_fetch_fails(monkeypatch):
    monkeypatch.setattr("src.tailoring.job_fetch.fetch_job_posting", lambda url, timeout=8.0: None)
    c = _login()
    resp = c.post("/api/jobs", data={"url": "https://example.com/unreachable"})
    assert resp.status_code == 422


def test_add_job_by_url_reuses_same_job_id_for_the_same_url(monkeypatch):
    # Pasting the same URL twice should update the same Application, not
    # create a duplicate row - _job_id_from_url is deterministic per URL.
    from src.tailoring.job_fetch import JobPosting

    fake_url = "https://boards.example.com/postings/duplicate-check"
    monkeypatch.setattr(
        "src.tailoring.job_fetch.fetch_job_posting",
        lambda url, timeout=8.0: JobPosting(text="Some job text.", title_guess="Some Role", company_guess="example.com"),
    )
    monkeypatch.setattr(
        "src.tailoring.engine.tailor_resume_for_job",
        lambda job_id, job_title, job_description, required_skills: {
            "local_path": "x", "s3_key": None, "llm_tailored": False, "resume_filename": "some-role-1.pdf",
        },
    )

    c = _login()
    first = c.post("/api/jobs", data={"url": fake_url}).json()
    second = c.post("/api/jobs", data={"url": fake_url}).json()
    assert first["job_id"] == second["job_id"]


def test_login_page_prefills_on_every_deploy():
    # Direct feedback: prefill everywhere, including the live AWS deploy
    # (previously local-only — see login_form's docstring for the
    # tradeoff this confirmed choice accepts).
    resp = client.get("/login")
    assert 'value="admin"' in resp.text


def test_project_suggestions_endpoint():
    c = _login()
    resp = c.get("/api/project-suggestions", params={"skills": "React,Node.js", "count": 3})
    assert resp.status_code == 200
    body = resp.json()
    assert "suggestions" in body
    assert len(body["suggestions"]) > 0


def test_project_suggestions_endpoint_no_skills():
    c = _login()
    resp = c.get("/api/project-suggestions", params={"skills": ""})
    assert resp.status_code == 200
    assert resp.json()["suggestions"] == []


def test_projects_page_renders():
    c = _login()
    resp = c.get("/projects")
    assert resp.status_code == 200
    assert "Project Bank" in resp.text
    assert "WordPress" in resp.text  # a real stack from project_bank.json


def test_add_project_endpoint_does_not_touch_real_bank_file(monkeypatch):
    # save_project is imported lazily inside the route, so patching the
    # source module's attribute before the request is picked up — this
    # keeps the test from writing into the real project_bank.json.
    captured = {}

    def fake_save_project(record, **kwargs):
        captured["record"] = dict(record)
        record.setdefault("id", "fake-id")
        return record

    monkeypatch.setattr("src.common.project_bank.save_project", fake_save_project)

    c = _login()
    resp = c.post(
        "/api/project-bank",
        data={
            "title": "Test Project",
            "stack": "test_stack",
            "stack_label": "Test Stack",
            "category": "general",
            "tools": "Foo, Bar",
            "description": "A description.",
            "features": "Feature one\nFeature two",
            "estimated_effort": "1 week",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/projects"
    assert captured["record"]["title"] == "Test Project"
    assert captured["record"]["tools"] == ["Foo", "Bar"]
    assert captured["record"]["features"] == ["Feature one", "Feature two"]
    assert captured["record"]["stack"] == "test_stack"


def test_edit_project_form_renders(monkeypatch):
    fake_project = {
        "id": "fake-1", "stack": "x", "stack_label": "X", "category": "general",
        "tools": ["Foo", "Bar"], "title": "Fake Project", "description": "d",
        "features": ["f1", "f2"], "estimated_effort": "1 week",
    }
    monkeypatch.setattr("src.common.project_bank.get_project", lambda pid, **kw: fake_project if pid == "fake-1" else None)

    c = _login()
    resp = c.get("/projects/fake-1/edit")
    assert resp.status_code == 200
    assert "Fake Project" in resp.text
    assert "Foo, Bar" in resp.text


def test_edit_project_form_404_for_unknown_id(monkeypatch):
    monkeypatch.setattr("src.common.project_bank.get_project", lambda pid, **kw: None)
    c = _login()
    resp = c.get("/projects/does-not-exist/edit")
    assert resp.status_code == 404


def test_update_project_endpoint(monkeypatch):
    captured = {}

    def fake_save_project(record, **kwargs):
        captured["record"] = dict(record)
        return record

    monkeypatch.setattr("src.common.project_bank.save_project", fake_save_project)

    c = _login()
    resp = c.post(
        "/api/project-bank/existing-id",
        data={
            "title": "Updated Title", "stack": "x", "stack_label": "X",
            "category": "general", "tools": "A, B", "description": "d",
            "features": "f1\nf2", "estimated_effort": "1 week",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/projects"
    assert captured["record"]["id"] == "existing-id"  # preserved, not regenerated
    assert captured["record"]["title"] == "Updated Title"


def test_delete_project_endpoint(monkeypatch):
    deleted_ids = []
    monkeypatch.setattr("src.common.project_bank.delete_project", lambda pid, **kw: deleted_ids.append(pid) or True)

    c = _login()
    resp = c.post("/api/project-bank/some-id/delete", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/projects"
    assert deleted_ids == ["some-id"]


def test_delete_project_endpoint_404_when_not_found(monkeypatch):
    monkeypatch.setattr("src.common.project_bank.delete_project", lambda pid, **kw: False)
    c = _login()
    resp = c.post("/api/project-bank/missing/delete")
    assert resp.status_code == 404
