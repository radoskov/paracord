"""User-management service (owner-operated, in-app).

Distinct from the server-console bootstrap/reset scripts: these functions back the
owner-only admin API and write audit events for every change.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.security import Role, hash_password
from app.models.user import User
from app.services.audit import record_event

_VALID_ROLES = {str(Role.OWNER), str(Role.EDITOR), str(Role.READER)}


def _check_role(role: str) -> str:
    role = str(role)
    if role not in _VALID_ROLES:
        raise ValueError(f"Unknown role: {role!r}")
    return role


def list_users(db: Session) -> list[User]:
    """Return all user accounts, oldest first."""
    return list(db.scalars(select(User).order_by(User.created_at)))


def create_user(
    db: Session,
    *,
    username: str,
    password: str,
    role: str,
    actor_user_id: uuid.UUID,
) -> User:
    """Create a user account and record a ``user.created`` audit event."""
    username = (username or "").strip()
    if not username:
        raise ValueError("Username must not be empty")
    if not password:
        raise ValueError("Password must not be empty")
    role = _check_role(role)
    if db.scalar(select(User).where(User.username == username)):
        raise ValueError(f"User {username!r} already exists")

    user = User(username=username, password_hash=hash_password(password), role=role)
    db.add(user)
    db.flush()
    record_event(
        db,
        "user.created",
        actor_user_id=actor_user_id,
        entity_type="user",
        entity_id=str(user.id),
        details={"username": username, "role": role, "method": "admin_api"},
    )
    return user


def set_user_role(
    db: Session,
    *,
    user_id: uuid.UUID,
    role: str,
    actor_user_id: uuid.UUID,
) -> User:
    """Change a user's role and record a ``user.role_changed`` audit event."""
    role = _check_role(role)
    user = db.get(User, user_id)
    if user is None:
        raise LookupError(f"User {user_id} not found")

    previous_role = user.role
    if previous_role == Role.OWNER and role != Role.OWNER and _active_owner_count(db) <= 1:
        raise ValueError("Cannot demote the last active owner")

    user.role = role
    db.flush()
    record_event(
        db,
        "user.role_changed",
        actor_user_id=actor_user_id,
        entity_type="user",
        entity_id=str(user.id),
        details={"from": previous_role, "to": role},
    )
    return user


def disable_user(
    db: Session,
    *,
    user_id: uuid.UUID,
    actor_user_id: uuid.UUID,
) -> User:
    """Disable a user account and record a ``user.disabled`` audit event."""
    user = db.get(User, user_id)
    if user is None:
        raise LookupError(f"User {user_id} not found")

    if user.disabled_at is None and user.role == Role.OWNER and _active_owner_count(db) <= 1:
        raise ValueError("Cannot disable the last active owner")

    if user.disabled_at is None:
        user.disabled_at = datetime.now(UTC)
        db.flush()
        record_event(
            db,
            "user.disabled",
            actor_user_id=actor_user_id,
            entity_type="user",
            entity_id=str(user.id),
        )
    return user


def _active_owner_count(db: Session) -> int:
    return (
        db.scalar(
            select(func.count())
            .select_from(User)
            .where(User.role == Role.OWNER, User.disabled_at.is_(None))
        )
        or 0
    )
