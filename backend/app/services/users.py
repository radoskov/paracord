"""User-management service (owner-operated, in-app).

Distinct from the server-console bootstrap/reset scripts: these functions back the
owner-only admin API and write audit events for every change.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, func, select
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


def enable_user(
    db: Session,
    *,
    user_id: uuid.UUID,
    actor_user_id: uuid.UUID,
) -> User:
    """Re-enable a disabled user account and record a ``user.enabled`` audit event."""
    user = db.get(User, user_id)
    if user is None:
        raise LookupError(f"User {user_id} not found")

    if user.disabled_at is not None:
        user.disabled_at = None
        db.flush()
        record_event(
            db,
            "user.enabled",
            actor_user_id=actor_user_id,
            entity_type="user",
            entity_id=str(user.id),
        )
    return user


def delete_user(
    db: Session,
    *,
    user_id: uuid.UUID,
    actor_user_id: uuid.UUID,
) -> None:
    """Permanently delete a **disabled** user and its sessions (owner only).

    The account must be disabled first (a deliberate two-step: disable → re-enable or delete), and
    the last active owner can never be the target (it can't be disabled in the first place).
    """
    from app.models.session import UserSession

    user = db.get(User, user_id)
    if user is None:
        raise LookupError(f"User {user_id} not found")
    if user.disabled_at is None:
        raise ValueError("Disable the user before deleting it")
    db.execute(delete(UserSession).where(UserSession.user_id == user_id))
    username = user.username
    db.delete(user)
    db.flush()
    record_event(
        db,
        "user.deleted",
        actor_user_id=actor_user_id,
        entity_type="user",
        entity_id=str(user_id),
        details={"username": username},
    )


_PROFILE_FIELDS = {"display_name", "email"}


def update_profile(
    db: Session,
    *,
    user: User,
    changes: dict[str, str | None],
    actor_user_id: uuid.UUID,
) -> User:
    """Update a user's own editable profile fields (display name, email).

    The username and role are intentionally immutable here. Only keys in ``changes`` are
    touched, so an absent key is left unchanged while ``None``/"" clears the field.
    """
    applied: dict[str, str | None] = {}
    for key, value in changes.items():
        if key not in _PROFILE_FIELDS:
            raise ValueError(f"Field {key!r} is not editable")
        cleaned = value.strip() if isinstance(value, str) else value
        cleaned = cleaned or None
        if getattr(user, key) != cleaned:
            setattr(user, key, cleaned)
            applied[key] = cleaned
    if applied:
        db.flush()
        record_event(
            db,
            "user.profile_updated",
            actor_user_id=actor_user_id,
            entity_type="user",
            entity_id=str(user.id),
            details=applied,
        )
    return user


def reset_user_password(
    db: Session,
    *,
    user_id: uuid.UUID,
    new_password: str,
    actor_user_id: uuid.UUID,
) -> int:
    """Owner action: set a new password for another user and sign out all their sessions.

    Returns the number of sessions revoked. Distinct from self-service ``change_password``: no
    current-password check (the owner is acting on behalf of the account).
    """
    from app.services.auth import revoke_all_user_sessions

    if len(new_password or "") < 8:
        raise ValueError("New password must be at least 8 characters")
    user = db.get(User, user_id)
    if user is None:
        raise LookupError(f"User {user_id} not found")

    user.password_hash = hash_password(new_password)
    user.password_changed_at = datetime.now(UTC)
    revoked = revoke_all_user_sessions(db, user_id)
    db.flush()
    record_event(
        db,
        "user.password_reset",
        actor_user_id=actor_user_id,
        entity_type="user",
        entity_id=str(user.id),
        details={"sessions_revoked": revoked},
    )
    return revoked


def _active_owner_count(db: Session) -> int:
    return (
        db.scalar(
            select(func.count())
            .select_from(User)
            .where(User.role == Role.OWNER, User.disabled_at.is_(None))
        )
        or 0
    )
