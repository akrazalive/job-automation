import os

os.environ.setdefault("STORAGE_BACKEND", "local")

from fastapi.testclient import TestClient

from src.dashboard.app import app

client = TestClient(app)


def test_healthz():
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_dashboard_page_renders():
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Admin Dashboard" in resp.text


def test_api_summary():
    resp = client.get("/api/summary")
    assert resp.status_code == 200
    body = resp.json()
    assert "total" in body


def test_api_applications():
    resp = client.get("/api/applications")
    assert resp.status_code == 200
    body = resp.json()
    assert "items" in body
    assert isinstance(body["items"], list)


def test_api_applications_filter_by_status():
    resp = client.get("/api/applications", params={"status": "applied"})
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert all(item["status"] == "applied" for item in items)
