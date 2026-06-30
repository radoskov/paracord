"""Shared FastAPI dependencies."""

from collections.abc import Callable

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import Role
from app.db.session import get_db
from app.models.agent import Agent
from app.models.user import User
from app.services.auth import get_active_session, hash_token


def require_authenticated_user(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    """Return the current authenticated user or reject the request."""
    scheme, _, raw_token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not raw_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")

    session = get_active_session(db, raw_token.strip())
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Your session has expired or was signed out. Please sign in again.",
        )

    user = db.get(User, session.user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Your session is no longer valid. Please sign in again.",
        )
    if user.disabled_at is not None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Your account has been disabled by an administrator. "
            "Contact them to regain access.",
        )
    return user


def require_roles(*allowed_roles: str) -> Callable[..., User]:
    """Build a dependency that allows only the given roles.

    Usage: ``user = Depends(require_roles(Role.OWNER, Role.EDITOR))``.
    """
    allowed = {str(role) for role in allowed_roles}

    def dependency(user: User = Depends(require_authenticated_user)) -> User:
        if user.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient role for this operation",
            )
        return user

    return dependency


def require_admin(user: User = Depends(require_authenticated_user)) -> User:
    """Allow owner or admin accounts (general administration endpoints).

    Both can manage users (editors/readers), agents, AI settings and the audit log. The narrower
    owner-only gate (``require_owner``) plus the service-layer checks restrict the privileged
    subset (managing admins/owner) to the owner.
    """
    if user.role not in (Role.OWNER, Role.ADMIN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator role required",
        )
    return user


def require_owner(user: User = Depends(require_authenticated_user)) -> User:
    """Allow only the owner account (owner-exclusive operations, e.g. managing admins)."""
    if user.role != Role.OWNER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Owner role required",
        )
    return user


def require_agent_token(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> Agent:
    """Return the authenticated approved agent or reject with 401."""
    scheme, _, raw_token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not raw_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing agent bearer token"
        )
    agent = db.scalar(
        select(Agent).where(
            Agent.token_hash == hash_token(raw_token.strip()), Agent.status == "approved"
        )
    )
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or unapproved agent token"
        )
    # Liveness tracking (SPEC §9.3), throttled (E3): only stamp + commit when the last_seen value
    # is stale by more than a minute, so a busy agent doesn't write+commit on every request.
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    last = agent.last_seen_at
    if last is not None and last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    if last is None or now - last > timedelta(seconds=60):
        agent.last_seen_at = now
        db.commit()
    return agent
