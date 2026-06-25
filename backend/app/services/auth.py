"""Authentication session service."""

import functools
import hashlib
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import hash_password, verify_password
from app.models.session import UserSession
from app.models.user import User


def hash_token(token: str) -> str:
    """Hash a bearer token before storage or lookup."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _as_utc(value: datetime) -> datetime:
    """Normalize a datetime to aware UTC.

    Datetimes round-tripped through SQLite (and plain ``timestamp`` Postgres columns) come
    back naive even when the column is declared ``timezone=True``; treat those stored values
    as UTC so comparisons against ``datetime.now(UTC)`` never mix naive and aware datetimes.
    """
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


@functools.lru_cache(maxsize=1)
def _timing_equalizer_hash() -> str:
    """Throwaway bcrypt hash used to equalize timing on the no-user path."""
    return hash_password("paracord-no-such-account")  # pragma: allowlist secret


def authenticate_user(db: Session, username: str, password: str) -> User | None:
    """Return an active user when credentials are valid.

    On the unknown/disabled-user path we still run a bcrypt verification against a
    dummy hash so response time does not reveal whether the account exists
    (account-enumeration mitigation, SPECIFICATION.md 7.2.5).
    """
    user = db.scalar(select(User).where(User.username == username))
    if user is None or user.disabled_at is not None:
        verify_password(password, _timing_equalizer_hash())
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


def create_user_session(db: Session, user: User, *, ttl_minutes: int) -> tuple[str, UserSession]:
    """Create a revocable bearer-token session and return the raw token once."""
    token = secrets.token_urlsafe(32)
    session = UserSession(
        user_id=user.id,
        token_hash=hash_token(token),
        expires_at=datetime.now(UTC) + timedelta(minutes=ttl_minutes),
    )
    db.add(session)
    db.flush()
    return token, session


def get_active_session(db: Session, token: str) -> UserSession | None:
    """Return an active, unexpired session for a raw bearer token."""
    session = db.scalar(select(UserSession).where(UserSession.token_hash == hash_token(token)))
    if session is None or session.revoked_at is not None:
        return None
    if _as_utc(session.expires_at) <= datetime.now(UTC):
        return None
    return session


def revoke_token(db: Session, token: str) -> UserSession | None:
    """Revoke a bearer token if it identifies an active session."""
    session = get_active_session(db, token)
    if session is None:
        return None
    session.revoked_at = datetime.now(UTC)
    db.flush()
    return session
