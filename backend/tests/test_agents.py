"""Agent enrollment tests (M5)."""

from pathlib import Path

import pytest
from app.core.security import hash_password
from app.db.base import Base
from app.models.agent import Agent, AgentEnrollmentToken
from app.models.audit import AuditEvent
from app.models.user import User
from app.services.agents import approve_agent, enroll_agent, mint_enrollment_token
from app.services.auth import hash_token
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker


@pytest.fixture()
def db_session(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'agents.db'}")
    Base.metadata.create_all(
        bind=engine,
        tables=[
            User.__table__,
            Agent.__table__,
            AgentEnrollmentToken.__table__,
            AuditEvent.__table__,
        ],
    )
    session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    with session_local() as session:
        yield session


@pytest.fixture()
def owner(db_session) -> User:
    user = User(username="owner", password_hash=hash_password("secret"), role="owner")
    db_session.add(user)
    db_session.commit()
    return user


# --- service ----------------------------------------------------------------


def test_enroll_then_approve_flow(db_session, owner: User) -> None:
    raw, token = mint_enrollment_token(db_session, owner=owner)
    db_session.commit()
    assert token.token_hash == hash_token(raw)  # stored hashed, not plaintext

    agent = enroll_agent(db_session, token=raw, name="laptop")
    db_session.commit()
    assert agent.status == "pending"
    assert agent.token_hash is None  # no access token until approved
    assert db_session.get(AgentEnrollmentToken, token.id).used_by_agent_id == agent.id

    agent_token, approved = approve_agent(db_session, agent_id=agent.id, owner=owner)
    db_session.commit()
    assert approved.status == "approved"
    assert approved.token_hash == hash_token(agent_token)
    assert approved.approved_by_user_id == owner.id

    events = {e.event_type for e in db_session.scalars(select(AuditEvent)).all()}
    assert {"agent.enroll_token_issued", "agent.enroll_requested", "agent.approved"} <= events


def test_enrollment_token_is_single_use(db_session, owner: User) -> None:
    raw, _ = mint_enrollment_token(db_session, owner=owner)
    db_session.commit()
    enroll_agent(db_session, token=raw, name="first")
    db_session.commit()
    with pytest.raises(ValueError, match="already been used"):
        enroll_agent(db_session, token=raw, name="second")


def test_invalid_token_is_rejected(db_session, owner: User) -> None:
    with pytest.raises(ValueError, match="Invalid enrollment token"):
        enroll_agent(db_session, token="not-a-real-token", name="laptop")


def test_expired_token_is_rejected(db_session, owner: User) -> None:
    raw, _ = mint_enrollment_token(db_session, owner=owner, ttl_minutes=-1)  # already expired
    db_session.commit()
    with pytest.raises(ValueError, match="expired"):
        enroll_agent(db_session, token=raw, name="laptop")


def test_approving_non_pending_agent_is_rejected(db_session, owner: User) -> None:
    raw, _ = mint_enrollment_token(db_session, owner=owner)
    db_session.commit()
    agent = enroll_agent(db_session, token=raw, name="laptop")
    db_session.commit()
    approve_agent(db_session, agent_id=agent.id, owner=owner)
    db_session.commit()
    with pytest.raises(ValueError, match="not pending"):
        approve_agent(db_session, agent_id=agent.id, owner=owner)


# --- API --------------------------------------------------------------------


def test_agent_enrollment_api_flow(client, auth_headers) -> None:
    owner = auth_headers("owner")
    token = client.post("/api/v1/admin/agents/enroll-token", headers=owner).json()["token"]

    # enroll-request is unauthenticated (agent has no user session).
    enrolled = client.post("/api/v1/agents/enroll-request", json={"token": token, "name": "laptop"})
    assert enrolled.status_code == 202
    agent_id = enrolled.json()["agent_id"]
    assert enrolled.json()["status"] == "pending"

    approved = client.post(f"/api/v1/admin/agents/{agent_id}/approve", headers=owner)
    assert approved.status_code == 200
    assert approved.json()["agent_token"]
    assert approved.json()["status"] == "approved"


def test_enroll_token_requires_owner(client, auth_headers) -> None:
    assert (
        client.post("/api/v1/admin/agents/enroll-token", headers=auth_headers("editor")).status_code
        == 403
    )


def test_enroll_request_rejects_bad_token(client) -> None:
    r = client.post("/api/v1/agents/enroll-request", json={"token": "nope", "name": "x"})
    assert r.status_code == 400


def test_admin_list_agent_files(client, auth_headers, db) -> None:
    import uuid as _uuid

    from app.models.agent import Agent, AgentFile

    agent = Agent(name="ws", status="approved")
    db.add(agent)
    db.flush()
    db.add(
        AgentFile(
            agent_id=agent.id,
            local_file_id="local-1",
            sha256="a" * 64,
            size_bytes=10,
            display_path="paper.pdf",
        )
    )
    db.commit()

    listed = client.get(f"/api/v1/admin/agents/{agent.id}/files", headers=auth_headers("owner"))
    assert listed.status_code == 200
    body = listed.json()
    assert len(body) == 1
    assert body[0]["local_file_id"] == "local-1"
    assert body[0]["teleport_status"] == "none"

    # Non-owner is rejected; unknown agent is 404.
    assert (
        client.get(
            f"/api/v1/admin/agents/{agent.id}/files", headers=auth_headers("editor")
        ).status_code
        == 403
    )
    assert (
        client.get(
            f"/api/v1/admin/agents/{_uuid.uuid4()}/files", headers=auth_headers("owner")
        ).status_code
        == 404
    )


# ---------------------------------------------------------------------------
# Per-agent privileges (§32.8)
# ---------------------------------------------------------------------------


def _approved_agent(db):
    from app.models.agent import Agent

    agent = Agent(name="ws", status="approved")
    db.add(agent)
    db.commit()
    return agent


def test_agent_privilege_defaults(client, auth_headers, db) -> None:
    agent = _approved_agent(db)
    body = next(
        a
        for a in client.get("/api/v1/admin/agents", headers=auth_headers("owner")).json()
        if a["id"] == str(agent.id)
    )
    assert body["can_index"] is True
    assert body["can_extract"] is True
    assert body["can_be_requested"] is True
    assert body["processing_visibility"] is True
    assert body["server_status_visibility"] is True
    assert body["can_teleport"] is False  # opt-in


def test_update_agent_privileges(client, auth_headers, db) -> None:
    agent = _approved_agent(db)
    owner = auth_headers("owner")
    updated = client.patch(
        f"/api/v1/admin/agents/{agent.id}/privileges", headers=owner, json={"can_teleport": True}
    )
    assert updated.status_code == 200
    assert updated.json()["can_teleport"] is True
    # Authz + not-found.
    assert (
        client.patch(
            f"/api/v1/admin/agents/{agent.id}/privileges",
            headers=auth_headers("editor"),
            json={"can_teleport": False},
        ).status_code
        == 403
    )


def test_manifest_requires_can_index(client, auth_headers, db) -> None:
    import secrets as _secrets

    from app.models.agent import Agent
    from app.services.auth import hash_token

    raw = _secrets.token_urlsafe(16)
    agent = Agent(name="ws", status="approved", token_hash=hash_token(raw), can_index=False)
    db.add(agent)
    db.commit()
    r = client.post(
        "/api/v1/agents/manifest",
        headers={"Authorization": f"Bearer {raw}"},
        json={"items": []},
    )
    assert r.status_code == 403


def test_request_teleport_requires_can_be_requested(client, auth_headers, db) -> None:
    from app.models.agent import Agent

    agent = Agent(name="ws", status="approved", can_be_requested=False)
    db.add(agent)
    db.commit()
    r = client.post(
        "/api/v1/imports/teleport",
        headers=auth_headers("editor"),
        json={"agent_id": str(agent.id), "local_file_id": "x"},
    )
    assert r.status_code == 403
