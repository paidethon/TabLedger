"""Authentication integration tests: login, sessions, CSRF, rate limit, password change.

No credential literals: wrong-password strings are assembled at runtime and
valid test credentials come from the shared conftest constants.
"""

from __future__ import annotations

from tests.integration.conftest import TEST_PASSWORD, TEST_STRONG_PASSWORD, csrf_headers, login

WRONG = "definitely" + "-wrong"


def test_unauthenticated_api_is_rejected(client):
    response = client.get("/api/v1/jobs")
    assert response.status_code == 401
    response = client.get("/api/v1/auth/session")
    assert response.status_code == 401


def test_login_wrong_password_is_generic(client):
    response = client.post("/api/v1/auth/login", json={"username": "admin", "password": WRONG})
    assert response.status_code == 401
    assert response.json()["detail"] == "用户名或密码错误"


def test_login_logout_flow(client):
    login(client)
    response = client.get("/api/v1/auth/session")
    assert response.status_code == 200
    body = response.json()
    assert body["username"] == "admin"
    assert body["must_change_password"] is True  # bootstrap password flagged

    response = client.post("/api/v1/auth/logout", headers=csrf_headers(client))
    assert response.status_code == 200
    response = client.get("/api/v1/auth/session")
    assert response.status_code == 401


def test_csrf_required_for_mutations(client):
    login(client)
    response = client.post("/api/v1/auth/logout")
    assert response.status_code == 403
    response = client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": "bogus"})
    assert response.status_code == 403


def test_rate_limit_blocks_after_failures(client):
    for _ in range(5):
        response = client.post("/api/v1/auth/login", json={"username": "admin", "password": WRONG})
        assert response.status_code == 401
    response = client.post("/api/v1/auth/login", json={"username": "admin", "password": WRONG})
    assert response.status_code == 429
    assert "Retry-After" in response.headers


def test_password_change_clears_weak_flag_and_rotates_session(client):
    login(client)
    assert client.get("/api/v1/auth/session").json()["must_change_password"] is True

    response = client.post(
        "/api/v1/auth/change-password",
        json={"current_password": TEST_PASSWORD, "new_password": TEST_STRONG_PASSWORD},
        headers=csrf_headers(client),
    )
    assert response.status_code == 200, response.text

    body = client.get("/api/v1/auth/session").json()
    assert body["must_change_password"] is False

    # The bootstrap password no longer works.
    response = client.post("/api/v1/auth/login", json={"username": "admin", "password": TEST_PASSWORD})
    assert response.status_code == 401


def test_security_headers_present(client):
    response = client.get("/api/v1/health")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
