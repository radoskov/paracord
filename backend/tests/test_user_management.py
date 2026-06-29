"""Role-authorization dependency and user-management service tests."""

import uuid
from pathlib import Path

import pytest
from app.api.deps import require_owner, require_roles
from app.core.security import Role, hash_password, verify_password
from app.db.base import Base
from app.models.audit import AuditEvent
from app.models.user import User
from app.services import users as user_service
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker


@pytest.fixture()
def db_session(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'users.db'}")
    Base.metadata.create_all(bind=engine, tables=[User.__table__, AuditEvent.__table__])
    session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    with session_local() as session:
        yield session


def _add_user(db_session, username: str, role: str) -> User:
    user = User(username=username, password_hash=hash_password("secret"), role=role)
    db_session.add(user)
    db_session.commit()
    return user


# --- authorization dependencies ---------------------------------------------


def test_require_owner_allows_owner_and_rejects_others(db_session) -> None:
    owner = _add_user(db_session, "owner", "owner")
    reader = _add_user(db_session, "reader", "reader")

    assert require_owner(user=owner) is owner

    with pytest.raises(HTTPException) as exc_info:
        require_owner(user=reader)
    assert exc_info.value.status_code == 403


def test_require_roles_checks_membership(db_session) -> None:
    editor = _add_user(db_session, "editor", "editor")
    reader = _add_user(db_session, "reader", "reader")
    editor_or_owner = require_roles(Role.OWNER, Role.EDITOR)

    assert editor_or_owner(user=editor) is editor
    with pytest.raises(HTTPException) as exc_info:
        editor_or_owner(user=reader)
    assert exc_info.value.status_code == 403


# --- user-management service ------------------------------------------------


def test_create_user_persists_and_audits(db_session) -> None:
    owner = _add_user(db_session, "owner", "owner")

    user = user_service.create_user(
        db_session, username="alice", password="pw", role=Role.EDITOR, actor_user_id=owner.id
    )
    db_session.commit()

    assert user.role == "editor"
    assert verify_password("pw", user.password_hash)
    events = db_session.scalars(
        select(AuditEvent).where(AuditEvent.event_type == "user.created")
    ).all()
    assert any(e.entity_id == str(user.id) for e in events)


def test_create_user_rejects_duplicate_and_bad_role(db_session) -> None:
    owner = _add_user(db_session, "owner", "owner")
    user_service.create_user(
        db_session, username="alice", password="pw", role=Role.READER, actor_user_id=owner.id
    )
    db_session.commit()

    with pytest.raises(ValueError, match="already exists"):
        user_service.create_user(
            db_session, username="alice", password="pw", role=Role.READER, actor_user_id=owner.id
        )
    with pytest.raises(ValueError, match="Unknown role"):
        user_service.create_user(
            db_session, username="bob", password="pw", role="superuser", actor_user_id=owner.id
        )


def test_set_user_role_changes_and_audits(db_session) -> None:
    owner = _add_user(db_session, "owner", "owner")
    alice = _add_user(db_session, "alice", "reader")

    user_service.set_user_role(
        db_session, user_id=alice.id, role=Role.EDITOR, actor_user_id=owner.id
    )
    db_session.commit()

    assert db_session.get(User, alice.id).role == "editor"
    changed = db_session.scalars(
        select(AuditEvent).where(AuditEvent.event_type == "user.role_changed")
    ).all()
    assert changed and changed[0].details["to"] == "editor"


def test_cannot_demote_or_disable_last_owner(db_session) -> None:
    owner = _add_user(db_session, "owner", "owner")

    with pytest.raises(ValueError, match="last active owner"):
        user_service.set_user_role(
            db_session, user_id=owner.id, role=Role.READER, actor_user_id=owner.id
        )
    with pytest.raises(ValueError, match="last active owner"):
        user_service.disable_user(db_session, user_id=owner.id, actor_user_id=owner.id)


def test_disable_user_sets_timestamp_and_audits(db_session) -> None:
    owner = _add_user(db_session, "owner", "owner")
    alice = _add_user(db_session, "alice", "reader")

    user_service.disable_user(db_session, user_id=alice.id, actor_user_id=owner.id)
    db_session.commit()

    assert db_session.get(User, alice.id).disabled_at is not None
    assert db_session.scalars(
        select(AuditEvent).where(AuditEvent.event_type == "user.disabled")
    ).all()


def test_enable_user_clears_timestamp_and_audits(db_session) -> None:
    owner = _add_user(db_session, "owner", "owner")
    alice = _add_user(db_session, "alice", "reader")

    user_service.disable_user(db_session, user_id=alice.id, actor_user_id=owner.id)
    db_session.commit()
    user_service.enable_user(db_session, user_id=alice.id, actor_user_id=owner.id)
    db_session.commit()

    assert db_session.get(User, alice.id).disabled_at is None
    assert db_session.scalars(
        select(AuditEvent).where(AuditEvent.event_type == "user.enabled")
    ).all()


def test_set_role_unknown_user_raises_lookup(db_session) -> None:
    owner = _add_user(db_session, "owner", "owner")
    with pytest.raises(LookupError):
        user_service.set_user_role(
            db_session, user_id=uuid.uuid4(), role=Role.READER, actor_user_id=owner.id
        )
