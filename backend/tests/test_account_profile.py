"""Account/profile fields: /auth/me, last_login_at, password_changed_at, agent lifecycle (§9.3)."""

from app.core.security import hash_password
from app.models.user import User
from sqlalchemy import select

# Clearly-fake test credentials.
_PW = "prof-pass-aaaa"  # pragma: allowlist secret
_PW_OLD = "old-pass-aaaa"  # pragma: allowlist secret
_PW_NEW = "new-pass-bbbb"  # pragma: allowlist secret


def _login(client, db, username="prof-owner", password=_PW):
    db.add(User(username=username, password_hash=hash_password(password), role="owner"))
    db.commit()
    token = client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}
    ).json()["access_token"]
    return {"Authorization": f"Bearer {token}"}, username


def test_auth_me_returns_identity(client, db):
    headers, username = _login(client, db)
    me = client.get("/api/v1/auth/me", headers=headers)
    assert me.status_code == 200
    body = me.json()
    assert body["username"] == username
    assert body["role"] == "owner"
    assert body["last_login_at"]  # set on the login above


def test_auth_me_requires_auth(client):
    assert client.get("/api/v1/auth/me").status_code == 401


def test_password_change_stamps_password_changed_at(client, db):
    headers, _ = _login(client, db, username="pw-user", password=_PW_OLD)
    r = client.post(
        "/api/v1/auth/change-password",
        headers=headers,
        json={"current_password": _PW_OLD, "new_password": _PW_NEW},
    )
    assert r.status_code == 200
    db.expire_all()
    user = db.scalar(select(User).where(User.username == "pw-user"))
    assert user.password_changed_at is not None


def test_theme_defaults_null_and_round_trips(client, auth_headers):
    h = auth_headers("owner")
    # NULL default: a fresh user has no theme preference.
    assert client.get("/api/v1/auth/me", headers=h).json()["theme"] is None
    # A known theme id round-trips through PATCH /auth/me + GET /auth/me.
    patched = client.patch("/api/v1/auth/me", headers=h, json={"theme": "mocha-cool"})
    assert patched.status_code == 200
    assert patched.json()["theme"] == "mocha-cool"
    assert client.get("/api/v1/auth/me", headers=h).json()["theme"] == "mocha-cool"
    # Explicit null resets to the boot default.
    client.patch("/api/v1/auth/me", headers=h, json={"theme": None})
    assert client.get("/api/v1/auth/me", headers=h).json()["theme"] is None


def test_theme_rejects_unknown_id(client, auth_headers):
    h = auth_headers("owner")
    # A well-formed but unknown id (neither bundled nor a custom theme) is rejected 400 by the
    # DB-aware service check (P4 moved membership validation there so custom-theme slugs pass).
    r = client.patch("/api/v1/auth/me", headers=h, json={"theme": "solarized-neon"})
    assert r.status_code == 400
    # A malformed id (bad slug format) is still rejected 422 by the schema validator.
    r2 = client.patch("/api/v1/auth/me", headers=h, json={"theme": "Bad Theme!"})
    assert r2.status_code == 422
    # The rejected value must not be persisted.
    assert client.get("/api/v1/auth/me", headers=h).json()["theme"] is None


def test_enrolled_agent_attributed_to_token_owner(client, auth_headers, db):
    owner_headers = auth_headers("owner")
    token = client.post("/api/v1/admin/agents/enroll-token", headers=owner_headers).json()["token"]
    agent_id = client.post(
        "/api/v1/agents/enroll-request", json={"token": token, "name": "lab-pc"}
    ).json()["agent_id"]

    from app.models.agent import Agent

    agent = db.get(Agent, __import__("uuid").UUID(agent_id))
    assert agent.created_by_user_id is not None  # attributed to the minting owner
