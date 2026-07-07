"""Auth-token / session + agent-token abuse probes (Batch S): session tokens are high-entropy and
stored only as an opaque hash; revoked/expired sessions are rejected; and an invalid, unapproved, or
under-privileged agent token is refused with 401/403. Enrollment tokens are single-use.
"""

from __future__ import annotations

import io
import uuid

import pytest
from app.models.agent import Agent
from app.services.auth import (
    create_user_session,
    get_active_session,
    hash_token,
    revoke_token,
)

pytestmark = pytest.mark.safety

_TINY_PDF = b"%PDF-1.4\n%%EOF\n"


# --- session token entropy + opacity -----------------------------------------------------------


def test_session_token_is_high_entropy_and_unique(db, make_user) -> None:
    user = make_user("entropy-user", role="reader")
    tokens = {create_user_session(db, user, ttl_minutes=60)[0] for _ in range(25)}
    db.commit()
    assert len(tokens) == 25  # no collisions
    for token in tokens:
        assert len(token) >= 32  # secrets.token_urlsafe(32) → ~43 url-safe chars
        assert token.isascii()


def test_session_stores_only_opaque_hash(db, make_user) -> None:
    user = make_user("opaque-user", role="reader")
    token, session = create_user_session(db, user, ttl_minutes=60)
    db.commit()
    assert session.token_hash != token
    assert session.token_hash == hash_token(token)
    # A caller presenting the stored hash (as if leaked from the DB) is NOT a valid session token.
    assert get_active_session(db, session.token_hash) is None


def test_revoked_session_is_rejected(client, db, make_user) -> None:
    user = make_user("revoked-user", role="reader")
    token, _ = create_user_session(db, user, ttl_minutes=60)
    db.commit()
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/v1/works", headers=headers).status_code == 200
    revoke_token(db, token)
    db.commit()
    assert client.get("/api/v1/works", headers=headers).status_code == 401


def test_expired_session_is_rejected(client, db, make_user) -> None:
    user = make_user("expired-user", role="reader")
    token, _ = create_user_session(db, user, ttl_minutes=-1)
    db.commit()
    assert (
        client.get("/api/v1/works", headers={"Authorization": f"Bearer {token}"}).status_code == 401
    )


def test_login_does_not_leak_token_in_body_metadata(client, db, make_user) -> None:
    make_user("login-user", role="reader", password="test-pass-1234")
    resp = client.post(
        "/api/v1/auth/login", json={"username": "login-user", "password": "test-pass-1234"}
    )
    assert resp.status_code == 200
    body = resp.json()
    # The token is only in the JSON body's access_token, never echoed in a URL/Location header.
    assert "access_token" in body
    assert "location" not in {k.lower() for k in resp.headers}


def test_garbage_and_missing_bearer_rejected(client) -> None:
    assert client.get("/api/v1/works").status_code == 401
    assert client.get("/api/v1/works", headers={"Authorization": "Bearer "}).status_code == 401
    assert client.get("/api/v1/works", headers={"Authorization": "Basic abc"}).status_code == 401
    assert (
        client.get("/api/v1/works", headers={"Authorization": "Bearer " + "x" * 40}).status_code
        == 401
    )


# --- agent-token abuse -------------------------------------------------------------------------


def _approved_agent(db, *, can_teleport: bool = False) -> tuple[str, Agent]:
    raw = "agent-" + uuid.uuid4().hex
    agent = Agent(
        name=f"a-{uuid.uuid4().hex[:6]}",
        status="approved",
        token_hash=hash_token(raw),
        can_teleport=can_teleport,
    )
    db.add(agent)
    db.commit()
    db.refresh(agent)
    return raw, agent


def test_invalid_agent_token_is_401(client) -> None:
    resp = client.get(
        "/api/v1/agents/me", headers={"Authorization": "Bearer not-a-real-agent-token"}
    )
    assert resp.status_code == 401


def test_unapproved_agent_token_is_401(client, db) -> None:
    raw = "pending-" + uuid.uuid4().hex
    agent = Agent(name="pending", status="pending", token_hash=hash_token(raw))
    db.add(agent)
    db.commit()
    resp = client.get("/api/v1/agents/me", headers={"Authorization": f"Bearer {raw}"})
    assert resp.status_code == 401


def test_revoked_status_agent_token_is_401(client, db) -> None:
    raw = "revoked-" + uuid.uuid4().hex
    agent = Agent(name="revoked", status="revoked", token_hash=hash_token(raw))
    db.add(agent)
    db.commit()
    resp = client.get("/api/v1/agents/me", headers={"Authorization": f"Bearer {raw}"})
    assert resp.status_code == 401


def test_approved_agent_token_is_accepted(client, db) -> None:
    raw, _agent = _approved_agent(db)
    resp = client.get("/api/v1/agents/me", headers={"Authorization": f"Bearer {raw}"})
    assert resp.status_code == 200


def test_agent_without_teleport_privilege_is_403(client, db) -> None:
    raw, _agent = _approved_agent(db, can_teleport=False)
    resp = client.post(
        f"/api/v1/agents/files/{uuid.uuid4().hex}/offer-teleport",
        headers={"Authorization": f"Bearer {raw}"},
        files={"file": ("x.pdf", io.BytesIO(_TINY_PDF), "application/pdf")},
    )
    assert resp.status_code == 403


def test_enrollment_token_is_single_use(client, db) -> None:
    from app.models.user import User
    from app.services.agents import mint_enrollment_token

    owner = User(username="enroll-owner", password_hash="x", role="owner")
    db.add(owner)
    db.commit()
    raw, _token = mint_enrollment_token(db, owner=owner)
    db.commit()
    first = client.post("/api/v1/agents/enroll-request", json={"token": raw, "name": "agent-one"})
    assert first.status_code == 202
    # Replaying the same enrollment token must be refused.
    second = client.post("/api/v1/agents/enroll-request", json={"token": raw, "name": "agent-two"})
    assert second.status_code == 400


def test_enrollment_rejects_unknown_token(client) -> None:
    resp = client.post("/api/v1/agents/enroll-request", json={"token": "bogus", "name": "agent-x"})
    assert resp.status_code == 400
