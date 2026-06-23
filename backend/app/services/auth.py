"""Authentication session service."""

from datetime import datetime, timedelta
import hashlib
import secrets

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import verify_password
from app.models.session import UserSession
from app.models.user import User


def hash_token(token: str) -> str:
    """Hash a bearer token before storage or lookup."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def authenticate_user(db: Session, username: str, password: str) -> User | None:
    """Return an active user when credentials are valid."""
    user = db.scalar(select(User).where(User.username == username))
    if user is None or user.disabled_at is not None:
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
        expires_at=datetime.utcnow() + timedelta(minutes=ttl_minutes),
    )
    db.add(session)
    db.flush()
    return token, session


def get_active_session(db: Session, token: str) -> UserSession | None:
    """Return an active, unexpired session for a raw bearer token."""
    session = db.scalar(select(UserSession).where(UserSession.token_hash == hash_token(token)))
    if session is None or session.revoked_at is not None:
        return None
    if session.expires_at <= datetime.utcnow():
        return None
    return session


def revoke_token(db: Session, token: str) -> UserSession | None:
    """Revoke a bearer token if it identifies an active session."""
    session = get_active_session(db, token)
    if session is None:
        return None
    session.revoked_at = datetime.utcnow()
    db.flush()
    return session
