"""Smoke tests that prove the API test harness (TestClient + SQLite) works."""


def test_health(client):
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_login_flow(client, make_user, default_password):
    make_user("owner", role="owner")
    r = client.post("/api/v1/auth/login", json={"username": "owner", "password": default_password})
    assert r.status_code == 200
    assert r.json()["access_token"]

    bad = client.post("/api/v1/auth/login", json={"username": "owner", "password": "wrong"})
    assert bad.status_code == 401

    missing = client.post("/api/v1/auth/login", json={"username": "ghost", "password": "x"})
    assert missing.status_code == 401


def test_admin_users_rbac(client, auth_headers):
    assert client.get("/api/v1/admin/users").status_code == 401  # no token
    assert client.get("/api/v1/admin/users", headers=auth_headers("reader")).status_code == 403
    assert client.get("/api/v1/admin/users", headers=auth_headers("editor")).status_code == 403
    assert client.get("/api/v1/admin/users", headers=auth_headers("owner")).status_code == 200
