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
    assert "Admin Dashboard" in resp.text


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


def test_login_page_prefills_locally():
    # LOCAL_ACTIONS_ENABLED is True in this test environment (no
    # AWS_LAMBDA_FUNCTION_NAME set), so the login form should be prefilled.
    resp = client.get("/login")
    assert 'value="admin"' in resp.text
