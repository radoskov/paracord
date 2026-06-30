"""Server-console admin script tests."""

from pathlib import Path

import pytest
from app.core.security import Role, verify_password
from app.db.base import Base
from app.models.audit import AuditEvent
from app.models.session import UserSession
from app.models.user import User
from app.services.auth import create_user_session
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from scripts import bootstrap_admin, reset_admin_password


@pytest.fixture()
def script_session(tmp_path: Path, monkeypatch):
    """Patch admin scripts to use an isolated SQLite database."""
    from sqlalchemy import create_engine

    engine = create_engine(f"sqlite:///{tmp_path / 'admin.db'}")
    session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    monkeypatch.setattr(bootstrap_admin, "engine", engine)
    monkeypatch.setattr(bootstrap_admin, "SessionLocal", session_local)
    monkeypatch.setattr(reset_admin_password, "engine", engine)
    monkeypatch.setattr(reset_admin_password, "SessionLocal", session_local)
    return session_local


def test_create_first_owner_records_audit_event(script_session) -> None:
    owner = bootstrap_admin.create_first_owner("owner", "secret-password")

    assert owner.username == "owner"
    assert owner.role == Role.OWNER
    assert verify_password("secret-password", owner.password_hash)

    with script_session() as session:
        events = session.scalars(select(AuditEvent)).all()

    # The owner is created with a personal group (Phase H), which also audits a group.created event.
    user_events = [e for e in events if e.event_type == "user.created"]
    assert len(user_events) == 1
    assert user_events[0].details["method"] == "server_console_bootstrap"
    assert {e.event_type for e in events} <= {"user.created", "group.created"}


def test_create_first_owner_refuses_second_owner(script_session) -> None:
    bootstrap_admin.create_first_owner("owner", "secret-password")

    with pytest.raises(RuntimeError, match="owner account already exists"):
        bootstrap_admin.create_first_owner("second-owner", "secret-password")


def test_reset_password_updates_hash_and_records_audit_event(script_session) -> None:
    bootstrap_admin.create_first_owner("owner", "old-password")
    Base.metadata.create_all(bind=script_session.kw["bind"])

    with script_session() as session:
        user = session.scalar(select(User).where(User.username == "owner"))
        assert user is not None
        create_user_session(session, user, ttl_minutes=60)
        session.commit()

    reset_admin_password.reset_password("owner", "new-password")

    with script_session() as session:
        user = session.scalar(select(User).where(User.username == "owner"))
        events = session.scalars(select(AuditEvent).order_by(AuditEvent.created_at)).all()
        sessions = session.scalars(select(UserSession)).all()

    assert user is not None
    assert verify_password("new-password", user.password_hash)
    assert not verify_password("old-password", user.password_hash)
    # Filter out the personal-group bootstrap event (Phase H) to assert the auth sequence.
    auth_events = [e for e in events if e.event_type != "group.created"]
    assert [event.event_type for event in auth_events] == [
        "user.created",
        "auth.password_reset_cli",
    ]
    assert events[-1].details["method"] == "server_console"
    assert events[-1].details["sessions_revoked"] is True
    assert events[-1].details["sessions_revoked_count"] == 1
    assert sessions[0].revoked_at is not None
