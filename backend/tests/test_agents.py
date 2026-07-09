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


def test_admin_rename_agent(client, auth_headers, db) -> None:
    from app.models.agent import Agent

    agent = Agent(name="old-name", status="approved")
    db.add(agent)
    db.commit()
    owner = auth_headers("owner")

    renamed = client.patch(
        f"/api/v1/admin/agents/{agent.id}", json={"name": "new-name"}, headers=owner
    )
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "new-name"

    # Blank names rejected; non-owner forbidden.
    assert (
        client.patch(
            f"/api/v1/admin/agents/{agent.id}", json={"name": "  "}, headers=owner
        ).status_code
        == 400
    )
    assert (
        client.patch(
            f"/api/v1/admin/agents/{agent.id}",
            json={"name": "x"},
            headers=auth_headers("editor"),
        ).status_code
        == 403
    )


def test_admin_delete_agent_removes_files(client, auth_headers, db) -> None:
    import uuid as _uuid

    from app.models.agent import Agent, AgentFile

    agent = Agent(name="doomed", status="approved")
    db.add(agent)
    db.flush()
    db.add(
        AgentFile(
            agent_id=agent.id, local_file_id="l1", sha256="b" * 64, size_bytes=1, display_path="p"
        )
    )
    db.commit()
    agent_id = agent.id
    owner = auth_headers("owner")

    deleted = client.delete(f"/api/v1/admin/agents/{agent_id}", headers=owner)
    assert deleted.status_code == 204

    db.expire_all()
    assert db.get(Agent, agent_id) is None
    assert db.scalar(select(AgentFile).where(AgentFile.agent_id == agent_id)) is None

    # Already gone → 404; non-owner forbidden.
    assert client.delete(f"/api/v1/admin/agents/{agent_id}", headers=owner).status_code == 404
    assert (
        client.delete(
            f"/api/v1/admin/agents/{_uuid.uuid4()}", headers=auth_headers("editor")
        ).status_code
        == 403
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


# ---------------------------------------------------------------------------
# Import actions, teleport reject/block, status (§32 S2)
# ---------------------------------------------------------------------------

import hashlib as _hashlib  # noqa: E402
import io as _io  # noqa: E402
import secrets as _secrets  # noqa: E402
from unittest.mock import patch as _patch  # noqa: E402

import fitz as _fitz  # noqa: E402


def _real_pdf_bytes() -> bytes:
    """A real, openable single-page PDF (the AUDIT E2 upload probe rejects header-only stubs)."""
    doc = _fitz.open()
    doc.new_page()
    data = doc.tobytes()
    doc.close()
    return data


_PDF = _real_pdf_bytes()
_PDF_SHA = _hashlib.sha256(_PDF).hexdigest()
_PREVIEW = {"page_count": 1, "preview_text": "Hello.", "text_layer_quality": "good"}


def _agent_with_token(db, **kw):
    from app.models.agent import Agent
    from app.services.auth import hash_token

    raw = _secrets.token_urlsafe(16)
    agent = Agent(name="ws", status="approved", token_hash=hash_token(raw), **kw)
    db.add(agent)
    db.commit()
    return agent, {"Authorization": f"Bearer {raw}"}


def test_manifest_stores_action_and_policy(client, db) -> None:
    from app.models.agent import AgentFile

    agent, headers = _agent_with_token(db)
    client.post(
        "/api/v1/agents/manifest",
        headers=headers,
        json={
            "items": [
                {
                    "local_file_id": "f1",
                    "sha256": _PDF_SHA,
                    "size_bytes": len(_PDF),
                    "virtual_path": "papers/x.pdf",
                    "import_action": "teleport",
                    "teleport_policy": "allow",
                }
            ]
        },
    )
    row = db.query(AgentFile).filter(AgentFile.local_file_id == "f1").first()
    assert row.import_action == "teleport"
    assert row.teleport_policy == "allow"
    assert row.virtual_path == "papers/x.pdf"


def test_extract_endpoint_indexes_and_marks_extracting(client, db) -> None:
    from app.core.config import get_settings
    from app.models.agent import AgentFile

    agent, headers = _agent_with_token(db)
    client.post(
        "/api/v1/agents/manifest",
        headers=headers,
        json={"items": [{"local_file_id": "f1", "sha256": _PDF_SHA, "size_bytes": len(_PDF)}]},
    )
    with (
        _patch("app.services.storage.get_settings", return_value=get_settings()),
        _patch("app.services.storage._extract_pdf_preview", return_value=_PREVIEW),
    ):
        r = client.post(
            "/api/v1/agents/files/f1/extract",
            headers=headers,
            files={"file": ("x.pdf", _io.BytesIO(_PDF), "application/pdf")},
        )
    assert r.status_code == 201
    row = db.query(AgentFile).filter(AgentFile.local_file_id == "f1").first()
    assert row.processing_state == "extracting"
    assert row.import_action == "index_and_extract"


def test_extract_requires_can_extract(client, db) -> None:
    agent, headers = _agent_with_token(db, can_extract=False)
    client.post(
        "/api/v1/agents/manifest",
        headers=headers,
        json={"items": [{"local_file_id": "f1", "sha256": _PDF_SHA, "size_bytes": len(_PDF)}]},
    )
    r = client.post(
        "/api/v1/agents/files/f1/extract",
        headers=headers,
        files={"file": ("x.pdf", _io.BytesIO(_PDF), "application/pdf")},
    )
    assert r.status_code == 403


def test_reject_forever_blocks_until_unblocked(client, auth_headers, db) -> None:
    agent, headers = _agent_with_token(db)
    owner = auth_headers("owner")
    client.post(
        "/api/v1/agents/manifest",
        headers=headers,
        json={"items": [{"local_file_id": "f1", "sha256": _PDF_SHA, "size_bytes": len(_PDF)}]},
    )
    body = {"agent_id": str(agent.id), "local_file_id": "f1"}
    assert client.post("/api/v1/imports/teleport", headers=owner, json=body).status_code == 202
    # Reject forever -> blocked.
    rej = client.post("/api/v1/agents/teleports/f1/reject", headers=headers, json={"forever": True})
    assert rej.status_code == 200 and rej.json()["blocked"] is True
    # Re-request is refused while blocked.
    assert client.post("/api/v1/imports/teleport", headers=owner, json=body).status_code == 409
    # Unblock -> requestable again.
    assert client.post("/api/v1/agents/teleports/f1/unblock", headers=headers).status_code == 200
    assert client.post("/api/v1/imports/teleport", headers=owner, json=body).status_code == 202


def test_agent_file_status_endpoint(client, db) -> None:
    agent, headers = _agent_with_token(db)
    client.post(
        "/api/v1/agents/manifest",
        headers=headers,
        json={"items": [{"local_file_id": "f1", "sha256": _PDF_SHA, "size_bytes": len(_PDF)}]},
    )
    rows = client.get("/api/v1/agents/files", headers=headers).json()
    assert [r["local_file_id"] for r in rows] == ["f1"]
    assert rows[0]["processing_state"] == "indexed"

    _agent2, no_vis = _agent_with_token(db, processing_visibility=False)
    assert client.get("/api/v1/agents/files", headers=no_vis).status_code == 403


def test_discard_after_extract_removes_file_keeps_work(db, tmp_path) -> None:
    from app.core.config import get_settings
    from app.models.agent import Agent, AgentFile
    from app.models.file import File, Location
    from app.services.agent_files import discard_after_extract
    from app.services.storage import content_addressed_path

    managed_root = tmp_path / "library"
    pdf_path = content_addressed_path(managed_root, _PDF_SHA)
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_path.write_bytes(_PDF)

    agent = Agent(name="ws", status="approved")
    file = File(
        sha256=_PDF_SHA, size_bytes=len(_PDF), mime_type="application/pdf", status="available"
    )
    db.add_all([agent, file])
    db.flush()
    db.add_all(
        [
            Location(file_id=file.id, location_type="managed_path", internal_uri=str(pdf_path)),
            AgentFile(
                agent_id=agent.id,
                local_file_id="f1",
                sha256=_PDF_SHA,
                size_bytes=len(_PDF),
                import_action="index_and_extract",
                processing_state="extracting",
                file_id=file.id,
            ),
        ]
    )
    db.commit()

    settings = get_settings().model_copy(update={"managed_library_root": str(managed_root)})
    assert discard_after_extract(db, file=file, settings=settings) is True
    db.commit()

    assert not pdf_path.exists()
    assert file.status == "extracted_discarded"
    row = db.query(AgentFile).filter(AgentFile.local_file_id == "f1").first()
    assert row.processing_state == "extracted"
    assert row.file_id is None


def test_file_status_includes_title_and_authors(client, db) -> None:
    """#11: the file-status endpoint returns the linked Work's title + best authors assertion."""
    from app.models.agent import AgentFile
    from app.models.file import File, FileWorkLink
    from app.models.metadata import MetadataAssertion
    from app.models.work import Work

    agent, headers = _agent_with_token(db)
    file = File(sha256="c" * 64, size_bytes=1, mime_type="application/pdf", status="available")
    work = Work(canonical_title="Deep Nets", normalized_title="deep nets")
    db.add_all([file, work])
    db.flush()
    db.add_all(
        [
            FileWorkLink(file_id=file.id, work_id=work.id, user_confirmed=False),
            MetadataAssertion(
                entity_type="work",
                entity_id=work.id,
                field_name="authors",
                value="Jane Doe; John Roe",
                source="grobid",
                confidence=0.9,
                selected_as_canonical=True,
            ),
            AgentFile(
                agent_id=agent.id,
                local_file_id="m1",
                sha256="c" * 64,
                size_bytes=1,
                display_path="deep.pdf",
                file_id=file.id,
            ),
        ]
    )
    db.commit()

    rows = client.get("/api/v1/agents/files", headers=headers).json()
    row = next(r for r in rows if r["local_file_id"] == "m1")
    assert row["extracted_title"] == "Deep Nets"
    assert row["extracted_authors"] == "Jane Doe; John Roe"


def test_offer_teleport_requires_can_teleport(client, db) -> None:
    """#12: agent-initiated teleport is rejected unless can_teleport is granted."""
    agent, headers = _agent_with_token(db, can_teleport=False)
    r = client.post(
        "/api/v1/agents/files/o1/offer-teleport",
        headers=headers,
        files={"file": ("x.pdf", _io.BytesIO(_PDF), "application/pdf")},
    )
    assert r.status_code == 403


def test_offer_teleport_stores_file_and_work(client, db) -> None:
    """#12: a granted agent can push a file directly; it is stored + linked to a Work."""
    from app.core.config import get_settings
    from app.models.agent import AgentFile

    agent, headers = _agent_with_token(db, can_teleport=True)
    with (
        _patch("app.services.storage.get_settings", return_value=get_settings()),
        _patch("app.services.storage._extract_pdf_preview", return_value=_PREVIEW),
    ):
        r = client.post(
            "/api/v1/agents/files/o1/offer-teleport",
            headers=headers,
            files={"file": ("offered.pdf", _io.BytesIO(_PDF), "application/pdf")},
        )
    assert r.status_code == 201
    assert r.json()["status"] == "complete"
    row = db.query(AgentFile).filter(AgentFile.local_file_id == "o1").first()
    assert row.teleport_status == "complete"
    assert row.processing_state == "teleported"
    assert row.file_id is not None


def test_offer_teleport_reuses_index_only_stub(client, db) -> None:
    """1a: an agent-initiated teleport of a previously index_only file enriches its existing stub
    paper in place rather than creating a second Work (mirrors complete_teleport)."""
    from app.core.config import get_settings
    from app.models.agent import AgentFile
    from app.models.work import Work
    from sqlalchemy import func, select

    agent, headers = _agent_with_token(db, can_teleport=True)
    # 1) Scan the file as index_only → creates a single stub Work linked via AgentFile.work_id.
    item = {
        "local_file_id": "t1",
        "sha256": _PDF_SHA,
        "size_bytes": len(_PDF),
        "virtual_path": "papers/attention_is_all_you_need.pdf",
        "import_action": "index_only",
    }
    client.post("/api/v1/agents/manifest", headers=headers, json={"items": [item]})
    stub_id = db.scalar(select(AgentFile.work_id).where(AgentFile.local_file_id == "t1"))
    assert stub_id is not None
    assert db.scalar(select(func.count()).select_from(Work)) == 1

    # 2) Offer-teleport the same file → must reuse the stub, not create a duplicate.
    with (
        _patch("app.services.storage.get_settings", return_value=get_settings()),
        _patch("app.services.storage._extract_pdf_preview", return_value=_PREVIEW),
    ):
        r = client.post(
            "/api/v1/agents/files/t1/offer-teleport",
            headers=headers,
            files={"file": ("attention_is_all_you_need.pdf", _io.BytesIO(_PDF), "application/pdf")},
        )
    assert r.status_code == 201
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(Work)) == 1  # no duplicate
    stub = db.get(Work, stub_id)
    assert stub.canonical_metadata_source == "teleport"  # promoted in place, marker cleared
    row = db.scalar(select(AgentFile).where(AgentFile.local_file_id == "t1"))
    assert row.file_id is not None


def test_agent_me_and_source_removed(client, db) -> None:
    from app.models.agent import AgentFile

    agent, headers = _agent_with_token(db)
    me = client.get("/api/v1/agents/me", headers=headers).json()
    assert me["status"] == "approved"
    assert me["can_index"] is True

    client.post(
        "/api/v1/agents/manifest",
        headers=headers,
        json={"items": [{"local_file_id": "g1", "sha256": _PDF_SHA, "size_bytes": len(_PDF)}]},
    )
    r = client.post(
        "/api/v1/agents/files/source-removed", headers=headers, json={"local_file_ids": ["g1"]}
    )
    assert r.status_code == 200 and r.json()["marked"] == 1
    row = db.query(AgentFile).filter(AgentFile.local_file_id == "g1").first()
    assert row.processing_state == "source_removed"


def test_index_only_manifest_creates_promotable_stub(client, db) -> None:
    """B6: an index_only entry creates a minimal library stub (badged not-extracted) linked to the
    agent file; re-scanning is idempotent (no duplicate stub)."""
    from app.models.agent import AgentFile
    from app.models.work import Work
    from sqlalchemy import func, select

    agent, headers = _agent_with_token(db)
    item = {
        "local_file_id": "s1",
        "sha256": _PDF_SHA,
        "size_bytes": len(_PDF),
        "virtual_path": "papers/attention_is_all_you_need.pdf",
        "import_action": "index_only",
    }
    assert (
        client.post("/api/v1/agents/manifest", headers=headers, json={"items": [item]}).status_code
        == 202
    )
    row = db.scalar(select(AgentFile).where(AgentFile.local_file_id == "s1"))
    assert row.work_id is not None
    stub = db.get(Work, row.work_id)
    assert stub.canonical_metadata_source == "agent_index_only"  # badged "not extracted"
    assert "attention" in (stub.canonical_title or "").lower()  # filename-derived title
    # Re-scan → no second stub.
    client.post("/api/v1/agents/manifest", headers=headers, json={"items": [item]})
    assert db.scalar(select(func.count()).select_from(Work)) == 1


def test_index_only_stub_suppressed_when_toggle_off(client, db) -> None:
    """B6: create_stubs=False (agent toggle off) → index_only records the file only, no stub."""
    from app.models.work import Work
    from sqlalchemy import func, select

    _agent, headers = _agent_with_token(db)
    client.post(
        "/api/v1/agents/manifest",
        headers=headers,
        json={
            "items": [
                {
                    "local_file_id": "s2",
                    "sha256": _PDF_SHA,
                    "size_bytes": len(_PDF),
                    "import_action": "index_only",
                }
            ],
            "create_stubs": False,
        },
    )
    assert db.scalar(select(func.count()).select_from(Work)) == 0


def test_index_only_manifest_reuses_existing_file_by_hash(client, db) -> None:
    """Issue 6: an index_only scan of content whose File already exists (e.g. a prior teleport
    already promoted to its real title) must attach to that Work, not mint a filename-titled
    duplicate paper alongside the properly-titled one."""
    from app.core.config import get_settings
    from app.models.agent import AgentFile
    from app.models.file import FileWorkLink
    from app.models.work import Work
    from sqlalchemy import func, select

    agent, headers = _agent_with_token(db, can_teleport=True)

    # 1) The content already exists on the server under a properly-extracted title (simulates a
    # prior teleport/manual upload followed by extraction, or a different agent's earlier push).
    # offer_teleport links the Work via FileWorkLink, not AgentFile.work_id (that field is only
    # ever set by the index_only stub path being tested below).
    with (
        _patch("app.services.storage.get_settings", return_value=get_settings()),
        _patch("app.services.storage._extract_pdf_preview", return_value=_PREVIEW),
    ):
        r = client.post(
            "/api/v1/agents/files/existing1/offer-teleport",
            headers=headers,
            files={"file": ("attention_is_all_you_need.pdf", _io.BytesIO(_PDF), "application/pdf")},
        )
    assert r.status_code == 201
    db.expire_all()
    existing_row = db.scalar(select(AgentFile).where(AgentFile.local_file_id == "existing1"))
    existing_file_id = existing_row.file_id
    assert existing_file_id is not None
    existing_work_id = db.scalar(
        select(FileWorkLink.work_id).where(FileWorkLink.file_id == existing_file_id)
    )
    work = db.get(Work, existing_work_id)
    work.canonical_title = "Attention Is All You Need"
    work.canonical_metadata_source = "extraction"
    db.commit()

    # 2) A fresh manifest entry — never scanned before by this (or any) agent file row — reports
    # the identical content (same sha256) as index_only. Before the fix this always minted a
    # second, filename-titled stub Work.
    item = {
        "local_file_id": "new-scan-1",
        "sha256": _PDF_SHA,
        "size_bytes": len(_PDF),
        "virtual_path": "papers/attention_is_all_you_need.pdf",
        "import_action": "index_only",
    }
    assert (
        client.post("/api/v1/agents/manifest", headers=headers, json={"items": [item]}).status_code
        == 202
    )
    db.expire_all()

    assert db.scalar(select(func.count()).select_from(Work)) == 1  # no duplicate Work
    new_row = db.scalar(select(AgentFile).where(AgentFile.local_file_id == "new-scan-1"))
    assert new_row.work_id == existing_work_id
    assert new_row.file_id == existing_file_id
    work = db.get(Work, existing_work_id)
    assert work.canonical_title == "Attention Is All You Need"  # untouched, not reverted


def test_deleting_stub_removes_agent_file(client, auth_headers, db) -> None:
    """B6/Q4: deleting the stub paper on the server drops the linked agent file, so it vanishes from
    the agent's server view (a reverse-sync Reconcile then un-indexes it locally)."""
    from app.models.agent import AgentFile
    from sqlalchemy import select

    agent, headers = _agent_with_token(db)
    client.post(
        "/api/v1/agents/manifest",
        headers=headers,
        json={
            "items": [
                {
                    "local_file_id": "s3",
                    "sha256": _PDF_SHA,
                    "size_bytes": len(_PDF),
                    "import_action": "index_only",
                }
            ]
        },
    )
    row = db.scalar(select(AgentFile).where(AgentFile.local_file_id == "s3"))
    work_id = row.work_id
    assert work_id is not None
    r = client.delete(f"/api/v1/works/{work_id}", headers=auth_headers("owner"))
    assert r.status_code in (200, 204)
    db.expire_all()
    assert db.scalar(select(AgentFile).where(AgentFile.local_file_id == "s3")) is None
