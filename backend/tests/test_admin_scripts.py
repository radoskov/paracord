"""Server-console admin script tests."""

from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.core.security import Role, verify_password
from app.models.audit import AuditEvent
from app.models.user import User
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

    assert len(events) == 1
    assert events[0].event_type == "user.created"
    assert events[0].details["method"] == "server_console_bootstrap"


def test_create_first_owner_refuses_second_owner(script_session) -> None:
    bootstrap_admin.create_first_owner("owner", "secret-password")

    with pytest.raises(RuntimeError, match="owner account already exists"):
        bootstrap_admin.create_first_owner("second-owner", "secret-password")


def test_reset_password_updates_hash_and_records_audit_event(script_session) -> None:
    bootstrap_admin.create_first_owner("owner", "old-password")

    reset_admin_password.reset_password("owner", "new-password")

    with script_session() as session:
        user = session.scalar(select(User).where(User.username == "owner"))
        events = session.scalars(select(AuditEvent).order_by(AuditEvent.created_at)).all()

    assert user is not None
    assert verify_password("new-password", user.password_hash)
    assert not verify_password("old-password", user.password_hash)
    assert [event.event_type for event in events] == ["user.created", "auth.password_reset_cli"]
    assert events[-1].details["method"] == "server_console"
