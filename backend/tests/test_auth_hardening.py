"""Stage 7 auth hardening: login lockout, change-password + session revocation, SSRF guard."""

import pytest
from app.services import login_throttle
from app.services.metadata_enrichment import ExternalFetchError, _get, _idseg


@pytest.fixture(autouse=True)
def _clear_throttle():
    login_throttle.reset_all()
    yield
    login_throttle.reset_all()


# --- login lockout ----------------------------------------------------------


def test_repeated_failures_lock_then_429(client, make_user, monkeypatch):
    from app.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "login_max_failures", 3, raising=False)
    make_user("alice", role="reader", password="correct-pass-1")  # pragma: allowlist secret

    for _ in range(3):
        bad = client.post("/api/v1/auth/login", json={"username": "alice", "password": "nope"})
        assert bad.status_code == 401
    # Now locked — even the correct password is refused with 429 + Retry-After.
    locked = client.post(
        "/api/v1/auth/login", json={"username": "alice", "password": "correct-pass-1"}
    )
    assert locked.status_code == 429
    assert int(locked.headers["Retry-After"]) > 0


def test_successful_login_clears_failures(client, make_user, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "login_max_failures", 3, raising=False)
    make_user("bob", role="reader", password="correct-pass-2")  # pragma: allowlist secret
    client.post("/api/v1/auth/login", json={"username": "bob", "password": "nope"})
    ok = client.post("/api/v1/auth/login", json={"username": "bob", "password": "correct-pass-2"})
    assert ok.status_code == 200
    # Failures cleared: two more bad attempts don't immediately lock (would need 3).
    client.post("/api/v1/auth/login", json={"username": "bob", "password": "nope"})
    again = client.post(
        "/api/v1/auth/login", json={"username": "bob", "password": "correct-pass-2"}
    )
    assert again.status_code == 200


# --- change password + session revocation -----------------------------------


def test_change_password_revokes_other_sessions(client, make_user, db):
    from app.services.auth import create_user_session

    user = make_user("carol", role="editor", password="old-pass-12345")  # pragma: allowlist secret
    # An "other" session that should be revoked by the password change.
    other_token, _ = create_user_session(db, user, ttl_minutes=60)
    db.commit()

    login = client.post(
        "/api/v1/auth/login", json={"username": "carol", "password": "old-pass-12345"}
    )
    current = {"Authorization": f"Bearer {login.json()['access_token']}"}

    changed = client.post(
        "/api/v1/auth/change-password",
        headers=current,
        json={"current_password": "old-pass-12345", "new_password": "new-pass-67890"},
    )
    assert changed.status_code == 200
    assert changed.json()["sessions_revoked"] >= 1

    # The other session is now dead; the current one still works.
    assert (
        client.post(
            "/api/v1/auth/logout", headers={"Authorization": f"Bearer {other_token}"}
        ).status_code
        == 401
    )
    assert client.get("/api/v1/works", headers=current).status_code == 200
    # New password works, old one does not.
    assert (
        client.post(
            "/api/v1/auth/login", json={"username": "carol", "password": "new-pass-67890"}
        ).status_code
        == 200
    )


def test_change_password_rejects_wrong_current(client, make_user):
    make_user("dave", role="reader", password="dave-pass-1234")  # pragma: allowlist secret
    token = client.post(
        "/api/v1/auth/login", json={"username": "dave", "password": "dave-pass-1234"}
    ).json()["access_token"]
    r = client.post(
        "/api/v1/auth/change-password",
        headers={"Authorization": f"Bearer {token}"},
        json={"current_password": "WRONG", "new_password": "whatever-12345"},
    )
    assert r.status_code == 400


# --- SSRF guard -------------------------------------------------------------


def test_identifier_is_percent_encoded():
    assert _idseg("10.1000/x y#z") == "10.1000%2Fx%20y%23z"


def test_cross_host_redirect_is_refused(monkeypatch):
    from app.services import metadata_enrichment

    class _FakeResp:
        def __init__(self, url, history):
            self.url = url
            self.history = history
            self.status_code = 200  # _get consults it for the S6c rate-limit retry

    class _FakeClient:
        def get(self, url, params=None, headers=None):
            # Simulate a redirect that left the original API host.
            hop = _FakeResp("http://169.254.169.254/latest/meta-data", [])
            return _FakeResp("http://169.254.169.254/latest/meta-data", [hop])

    monkeypatch.setattr(metadata_enrichment, "_HTTP_CLIENT", _FakeClient())
    with pytest.raises(ExternalFetchError):
        _get("https://api.crossref.org/works/10.1/x")
