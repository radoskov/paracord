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
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid bearer token")

    user = db.get(User, session.user_id)
    if user is None or user.disabled_at is not None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid bearer token")
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


def require_owner(user: User = Depends(require_authenticated_user)) -> User:
    """Allow only owner accounts (admin operations)."""
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
    # Liveness tracking (SPEC §9.3): stamp last_seen on each authenticated agent request.
    from datetime import UTC, datetime

    agent.last_seen_at = datetime.now(UTC)
    db.commit()
    return agent
