"""User-management service (owner-operated, in-app).

Distinct from the server-console bootstrap/reset scripts: these functions back the
owner-only admin API and write audit events for every change.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.security import Role, hash_password
from app.models.user import User
from app.services.audit import record_event

# Roles an admin endpoint may assign/create. ``owner`` is never assignable: it is the single,
# immutable bootstrap account. ``admin`` is assignable only by the owner (enforced separately).
_VALID_ROLES = {
    str(Role.ADMIN),
    str(Role.LIBRARIAN),
    str(Role.EDITOR),
    str(Role.CONTRIBUTOR),
    str(Role.READER),
}


def _check_role(role: str) -> str:
    role = str(role)
    if role == str(Role.OWNER):
        raise ValueError("The owner role cannot be assigned")
    if role not in _VALID_ROLES:
        raise ValueError(f"Unknown role: {role!r}")
    return role


class PermissionError403(Exception):
    """Raised when an actor is not permitted to manage the target account.

    Mapped to HTTP 403 by the admin endpoints.
    """


def _is_owner(user: User) -> bool:
    return user.role == Role.OWNER or bool(user.is_bootstrap)


def _guard_target(actor: User, target: User) -> None:
    """Enforce who may manage whom for disable/delete/role-change/reset.

    Rules (server-side, authoritative):
    - No account may target the owner at all (no disable/delete/role-change/reset of the owner).
    - Only the owner may target an ``admin`` account.
    - Everyone else (editor/reader) may be managed by both owner and admin.

    Self-disable / self-delete are blocked by the individual operations, not here.
    """
    if _is_owner(target):
        raise PermissionError403("The owner account cannot be modified")
    if target.role == Role.ADMIN and actor.role != Role.OWNER:
        raise PermissionError403("Only the owner can manage administrator accounts")


def list_users(db: Session) -> list[User]:
    """Return all user accounts, oldest first."""
    return list(db.scalars(select(User).order_by(User.created_at)))


def create_user(
    db: Session,
    *,
    username: str,
    password: str,
    role: str,
    actor: User,
) -> User:
    """Create a user account and record a ``user.created`` audit event.

    Only the owner may create ``admin`` accounts; the owner role can never be created here.
    """
    username = (username or "").strip()
    if not username:
        raise ValueError("Username must not be empty")
    if not password:
        raise ValueError("Password must not be empty")
    role = _check_role(role)
    if role == str(Role.ADMIN) and actor.role != Role.OWNER:
        raise PermissionError403("Only the owner can create administrator accounts")
    if db.scalar(select(User).where(User.username == username)):
        raise ValueError(f"User {username!r} already exists")

    user = User(username=username, password_hash=hash_password(password), role=role)
    db.add(user)
    db.flush()
    record_event(
        db,
        "user.created",
        actor_user_id=actor.id,
        entity_type="user",
        entity_id=str(user.id),
        details={"username": username, "role": role, "method": "admin_api"},
    )
    # Phase H: every user gets an auto-managed personal group (named == username) seeded with the
    # configured default grants.
    from app.services.groups import apply_default_grants, create_personal_group

    group = create_personal_group(db, user=user, actor=actor)
    apply_default_grants(db, group=group, actor=actor)
    return user


def set_user_role(
    db: Session,
    *,
    user_id: uuid.UUID,
    role: str,
    actor: User,
) -> User:
    """Change a user's role and record a ``user.role_changed`` audit event.

    The owner can never be role-changed; promoting someone to ``admin`` (or changing an existing
    admin's role) is owner-only.
    """
    role = _check_role(role)
    user = db.get(User, user_id)
    if user is None:
        raise LookupError(f"User {user_id} not found")

    _guard_target(actor, user)
    # Promoting to admin is also owner-only (creating an admin via role change).
    if role == str(Role.ADMIN) and actor.role != Role.OWNER:
        raise PermissionError403("Only the owner can grant the administrator role")

    previous_role = user.role
    user.role = role
    db.flush()
    record_event(
        db,
        "user.role_changed",
        actor_user_id=actor.id,
        entity_type="user",
        entity_id=str(user.id),
        details={"from": previous_role, "to": role},
    )
    return user


def disable_user(
    db: Session,
    *,
    user_id: uuid.UUID,
    actor: User,
) -> User:
    """Disable a user account and record a ``user.disabled`` audit event.

    No account may disable itself (prevents self-lockout), the owner can never be disabled, and
    only the owner may disable an admin.
    """
    user = db.get(User, user_id)
    if user is None:
        raise LookupError(f"User {user_id} not found")

    if user.id == actor.id:
        raise ValueError("You cannot disable your own account")
    _guard_target(actor, user)

    if user.disabled_at is None:
        user.disabled_at = datetime.now(UTC)
        db.flush()
        record_event(
            db,
            "user.disabled",
            actor_user_id=actor.id,
            entity_type="user",
            entity_id=str(user.id),
        )
    return user


def enable_user(
    db: Session,
    *,
    user_id: uuid.UUID,
    actor: User,
) -> User:
    """Re-enable a disabled user account and record a ``user.enabled`` audit event.

    Re-enabling an admin is owner-only (managing an admin account).
    """
    user = db.get(User, user_id)
    if user is None:
        raise LookupError(f"User {user_id} not found")

    _guard_target(actor, user)

    if user.disabled_at is not None:
        user.disabled_at = None
        db.flush()
        record_event(
            db,
            "user.enabled",
            actor_user_id=actor.id,
            entity_type="user",
            entity_id=str(user.id),
        )
    return user


def delete_user(
    db: Session,
    *,
    user_id: uuid.UUID,
    actor: User,
) -> None:
    """Permanently delete a **disabled** user and its sessions.

    The account must be disabled first (a deliberate two-step: disable → re-enable or delete). No
    account may delete itself, the owner can never be deleted (it can't be disabled in the first
    place), and only the owner may delete an admin.
    """
    from app.models.session import UserSession

    user = db.get(User, user_id)
    if user is None:
        raise LookupError(f"User {user_id} not found")
    if user.id == actor.id:
        raise ValueError("You cannot delete your own account")
    _guard_target(actor, user)
    if user.disabled_at is None:
        raise ValueError("Disable the user before deleting it")
    # Phase H: delete the user's personal group first (its memberships/grants cascade via FK).
    from app.services.groups import delete_personal_group

    delete_personal_group(db, user_id=user_id, actor=actor)
    db.execute(delete(UserSession).where(UserSession.user_id == user_id))
    username = user.username
    db.delete(user)
    db.flush()
    record_event(
        db,
        "user.deleted",
        actor_user_id=actor.id,
        entity_type="user",
        entity_id=str(user_id),
        details={"username": username},
    )


_PROFILE_FIELDS = {"display_name", "email", "papers_per_page"}


def update_profile(
    db: Session,
    *,
    user: User,
    changes: dict[str, str | None],
    actor_user_id: uuid.UUID,
) -> User:
    """Update a user's own editable profile fields (display name, email, papers per page).

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
    actor: User,
) -> int:
    """Admin action: set a new password for another user and sign out all their sessions.

    Returns the number of sessions revoked. Distinct from self-service ``change_password``: no
    current-password check (the admin is acting on behalf of the account). The owner's password
    can never be reset here (the owner manages it via their own profile), and resetting an admin's
    password is owner-only.
    """
    from app.services.auth import revoke_all_user_sessions

    if len(new_password or "") < 8:
        raise ValueError("New password must be at least 8 characters")
    user = db.get(User, user_id)
    if user is None:
        raise LookupError(f"User {user_id} not found")

    _guard_target(actor, user)

    user.password_hash = hash_password(new_password)
    user.password_changed_at = datetime.now(UTC)
    revoked = revoke_all_user_sessions(db, user_id)
    db.flush()
    record_event(
        db,
        "user.password_reset",
        actor_user_id=actor.id,
        entity_type="user",
        entity_id=str(user.id),
        details={"sessions_revoked": revoked},
    )
    return revoked
