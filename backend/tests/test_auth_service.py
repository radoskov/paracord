"""Authentication service tests."""

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.security import hash_password
from app.db.base import Base
from app.models.audit import AuditEvent
from app.models.session import UserSession
from app.models.user import User
from app.services.audit import record_event
from app.services.auth import (
    authenticate_user,
    create_user_session,
    get_active_session,
    hash_token,
    revoke_token,
)


@pytest.fixture()
def db_session(tmp_path: Path):
    """Create an isolated SQLite-backed session."""
    engine = create_engine(f"sqlite:///{tmp_path / 'auth.db'}")
    Base.metadata.create_all(
        bind=engine,
        tables=[User.__table__, AuditEvent.__table__, UserSession.__table__],
    )
    session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    with session_local() as session:
        yield session


def test_authenticate_user_validates_password(db_session) -> None:
    user = User(username="owner", password_hash=hash_password("secret"), role="owner")
    db_session.add(user)
    db_session.commit()

    assert authenticate_user(db_session, "owner", "secret") == user
    assert authenticate_user(db_session, "owner", "wrong") is None
    assert authenticate_user(db_session, "missing", "secret") is None


def test_create_and_revoke_user_session(db_session) -> None:
    user = User(username="owner", password_hash=hash_password("secret"), role="owner")
    db_session.add(user)
    db_session.commit()

    raw_token, session = create_user_session(db_session, user, ttl_minutes=60)
    db_session.commit()

    assert raw_token
    assert session.token_hash == hash_token(raw_token)
    assert session.token_hash != raw_token
    assert get_active_session(db_session, raw_token) is not None

    revoked = revoke_token(db_session, raw_token)
    db_session.commit()

    assert revoked is not None
    assert revoked.revoked_at is not None
    assert get_active_session(db_session, raw_token) is None


def test_record_event_persists_audit_event(db_session) -> None:
    event = record_event(
        db_session,
        "auth.login_failure",
        ip_address="127.0.0.1",
        details={"username": "owner"},
    )
    db_session.commit()

    assert event.id is not None
    assert db_session.get(AuditEvent, event.id).event_type == "auth.login_failure"
