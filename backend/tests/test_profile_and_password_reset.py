"""Self-service profile editing, admin password reset, and the disabled-account message."""

from app.services.auth import create_user_session

_NEW_PW = "reset-pass-zzzz"  # pragma: allowlist secret


def _token_for(
    db, make_user, username, role="reader", password="test-pass-1234"
):  # pragma: allowlist secret
    user = make_user(username, role=role, password=password)
    token, _ = create_user_session(db, user, ttl_minutes=60)
    db.commit()
    return user, {"Authorization": f"Bearer {token}"}


def test_update_own_profile(client, auth_headers):
    headers = auth_headers("editor")
    resp = client.patch(
        "/api/v1/auth/me",
        headers=headers,
        json={"display_name": "  Ada Lovelace  ", "email": "ada@example.org"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["display_name"] == "Ada Lovelace"  # trimmed
    assert body["email"] == "ada@example.org"

    # Persisted and reflected by GET /me.
    me = client.get("/api/v1/auth/me", headers=headers).json()
    assert me["display_name"] == "Ada Lovelace"


def test_profile_update_cannot_change_username_or_role(client, auth_headers):
    headers = auth_headers("reader")
    before = client.get("/api/v1/auth/me", headers=headers).json()
    resp = client.patch(
        "/api/v1/auth/me",
        headers=headers,
        json={"display_name": "Nickname", "username": "hacker", "role": "owner"},
    )
    assert resp.status_code == 200
    after = resp.json()
    assert after["username"] == before["username"]
    assert after["role"] == before["role"] == "reader"
    assert after["display_name"] == "Nickname"


def test_owner_resets_user_password_and_revokes_sessions(client, db, make_user, auth_headers):
    owner = auth_headers("owner")
    user, victim = _token_for(db, make_user, "needs-reset")

    resp = client.post(
        f"/api/v1/admin/users/{user.id}/reset-password",
        headers=owner,
        json={"new_password": _NEW_PW},
    )
    assert resp.status_code == 200
    assert resp.json()["sessions_revoked"] >= 1

    # The victim's old token is now rejected with the session-ended message.
    rejected = client.get("/api/v1/auth/me", headers=victim)
    assert rejected.status_code == 401

    # The new password works.
    login = client.post("/api/v1/auth/login", json={"username": "needs-reset", "password": _NEW_PW})
    assert login.status_code == 200


def test_reset_password_rejects_short_password(client, db, make_user, auth_headers):
    owner = auth_headers("owner")
    user, _ = _token_for(db, make_user, "short-pw-target")
    resp = client.post(
        f"/api/v1/admin/users/{user.id}/reset-password",
        headers=owner,
        json={"new_password": "short"},  # pragma: allowlist secret
    )
    assert resp.status_code == 400


def test_reset_password_is_owner_only(client, db, make_user, auth_headers):
    user, _ = _token_for(db, make_user, "editor-cant-reset-target")
    resp = client.post(
        f"/api/v1/admin/users/{user.id}/reset-password",
        headers=auth_headers("editor"),
        json={"new_password": _NEW_PW},
    )
    assert resp.status_code == 403


def test_disabled_account_gets_clear_message(client, db, make_user, auth_headers):
    owner = auth_headers("owner")
    user, victim = _token_for(db, make_user, "to-be-disabled")

    assert client.post(f"/api/v1/admin/users/{user.id}/disable", headers=owner).status_code == 200

    resp = client.get("/api/v1/auth/me", headers=victim)
    assert resp.status_code == 401
    assert "disabled" in resp.json()["detail"].lower()
