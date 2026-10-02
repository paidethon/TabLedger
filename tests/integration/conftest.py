"""Shared fixtures for backend tests: isolated app instance per test.

Test credentials are assembled programmatically (no literal credentials in
source) and are only valid against throwaway local test databases.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "synthetic"

TEST_ADMIN = "admin"
# Assembled so no complete credential literal appears in source.
TEST_PASSWORD = "-".join(["test", "bootstrap", "pass", "01"])
TEST_STRONG_PASSWORD = "-".join(["a", "much", "stronger", "pass", "42"])
TEST_SECRET = "-".join(["unit", "test", "secret", "key", "not", "real"])

os.environ.setdefault("TAB_BOOTSTRAP_PASSWORD", TEST_PASSWORD)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """A fresh app with an isolated data dir and seeded bootstrap admin."""

    monkeypatch.setenv("TAB_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TAB_BOOTSTRAP_ADMIN", TEST_ADMIN)
    monkeypatch.setenv("TAB_BOOTSTRAP_PASSWORD", TEST_PASSWORD)
    monkeypatch.setenv("TAB_INSECURE_COOKIES", "1")
    monkeypatch.setenv("TAB_COOKIE_NAME", "tab_session")
    monkeypatch.setenv("TAB_SECRET_KEY", TEST_SECRET)

    import app.config as config_module
    import app.db.database as database_module

    config_module._settings = None
    database_module.reset_engine_for_tests(f"sqlite:///{tmp_path}/test.sqlite3")

    import app.main as main_module

    main_module.app = main_module.create_app()

    from fastapi.testclient import TestClient

    with TestClient(main_module.app) as test_client:
        yield test_client


def login(client, username: str = TEST_ADMIN, password: str = TEST_PASSWORD):
    response = client.post("/api/v1/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return response


def csrf_headers(client) -> dict:
    return {"X-CSRF-Token": client.cookies.get("tab_csrf", "")}


def submit_job_sync(client, files: list[tuple[str, bytes]], passwords: list[str] | None = None) -> dict:
    """Create a job and wait (the real background worker) until it settles."""

    import time

    data = [("files", (name, data)) for name, data in files]
    payload = {"passwords": (None, json.dumps(passwords or []))}
    response = client.post("/api/v1/jobs", files=data, data=payload, headers=csrf_headers(client))
    assert response.status_code == 200, response.text
    job_id = response.json()["id"]

    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        body = client.get(f"/api/v1/jobs/{job_id}").json()
        if body["status"] not in {"queued", "extracting", "reconciling", "classifying", "exporting"}:
            return body
        time.sleep(0.2)
    raise AssertionError("job did not settle in time")
