"""Shared API dependency tests."""

from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.deps import require_authenticated_user
from app.core.security import hash_password
from app.db.base import Base
from app.models.session import UserSession
from app.models.user import User
from app.services.auth import create_user_session


@pytest.fixture()
def db_session(tmp_path: Path):
    """Create an isolated SQLite-backed session."""
    engine = create_engine(f"sqlite:///{tmp_path / 'api-deps.db'}")
    Base.metadata.create_all(bind=engine, tables=[User.__table__, UserSession.__table__])
    session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    with session_local() as session:
        yield session


def test_require_authenticated_user_accepts_active_bearer_token(db_session) -> None:
    user = User(username="owner", password_hash=hash_password("secret"), role="owner")
    db_session.add(user)
    db_session.commit()
    token, _session = create_user_session(db_session, user, ttl_minutes=60)
    db_session.commit()

    current_user = require_authenticated_user(authorization=f"Bearer {token}", db=db_session)

    assert current_user.id == user.id


def test_require_authenticated_user_rejects_missing_token(db_session) -> None:
    with pytest.raises(HTTPException) as exc_info:
        require_authenticated_user(authorization=None, db=db_session)

    assert exc_info.value.status_code == 401


def test_require_authenticated_user_rejects_invalid_token(db_session) -> None:
    with pytest.raises(HTTPException) as exc_info:
        require_authenticated_user(authorization="Bearer invalid", db=db_session)

    assert exc_info.value.status_code == 401
