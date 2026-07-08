"""User-group lifecycle + admin CRUD (Phase H access control).

Two layers:

* **Lifecycle** (called from ``app.services.users``): ``create_personal_group`` makes the
  auto-managed per-user group (named == username) on user create, ``apply_default_grants`` seeds it
  with the configured default-grant set, and personal groups are deleted on user delete (their
  memberships/grants cascade via FK).
* **Admin CRUD**: list/create/delete shared groups, manage membership, manage grants, and manage
  the default-grant set + default access level. Authorization is admin-or-owner (the endpoints use
  ``require_admin``); every mutation writes an audit event.

Personal groups can never be deleted directly via the admin API (they are tied to user lifecycle).
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.group import (
    GRANT_TARGET_TYPES,
    DefaultGrant,
    Group,
    GroupGrant,
    GroupMembership,
)
from app.models.organization import Rack, Shelf
from app.models.user import User
from app.services.audit import record_event
from app.utils.table_presence import table_present


class GroupError(ValueError):
    """Raised for invalid group operations (mapped to HTTP 400/409 by the endpoints)."""


def _groups_table_present(db: Session) -> bool:
    """Whether the Phase H ``groups`` table exists (narrow unit-test schemas / pre-migration may
    omit it; the lifecycle hooks then no-op rather than crash the caller's transaction)."""
    return table_present(db, Group.__tablename__)


# --------------------------------------------------------------------------------------------------
# Validation helpers
# --------------------------------------------------------------------------------------------------
def _check_target(db: Session, target_type: str, target_id: uuid.UUID) -> None:
    if target_type not in GRANT_TARGET_TYPES:
        raise GroupError(f"Unknown target type {target_type!r} (allowed: {GRANT_TARGET_TYPES})")
    model = Rack if target_type == "rack" else Shelf
    if db.get(model, target_id) is None:
        raise GroupError(f"{target_type.capitalize()} {target_id} not found")


# --------------------------------------------------------------------------------------------------
# Lifecycle (called from users service)
# --------------------------------------------------------------------------------------------------
def create_personal_group(db: Session, *, user: User, actor: User) -> Group | None:
    """Create the auto-managed personal group (named == username) for a freshly created user.

    Resolves a name collision (a shared group already using the username) deterministically by
    appending a short id suffix. Adds the membership row. Writes a ``group.created`` audit event.
    The caller commits. No-ops (returns ``None``) when the Phase H tables are absent (narrow
    unit-test schema / pre-migration bootstrap).
    """
    if not _groups_table_present(db):
        return None
    name = user.username
    if db.scalar(select(Group).where(Group.name == name)) is not None:
        suffix = str(user.id).replace("-", "")[:8]
        name = f"{user.username}-{suffix}"
        if db.scalar(select(Group).where(Group.name == name)) is not None:
            raise GroupError(f"Cannot create a personal group for {user.username!r}: name taken")
    group = Group(name=name, is_personal=True, personal_user_id=user.id)
    db.add(group)
    db.flush()
    db.add(GroupMembership(group_id=group.id, user_id=user.id, added_by_user_id=actor.id))
    db.flush()
    record_event(
        db,
        "group.created",
        actor_user_id=actor.id,
        entity_type="group",
        entity_id=str(group.id),
        details={"name": name, "is_personal": True, "personal_user_id": str(user.id)},
    )
    return group


def apply_default_grants(db: Session, *, group: Group | None, actor: User) -> int:
    """Grant the group every configured default target. Returns the number of grants created.

    Skips targets the group already has a grant for (idempotent). The caller commits. No-ops when
    the Phase H tables are absent or no group was created.
    """
    if group is None or not _groups_table_present(db):
        return 0
    defaults = list(db.scalars(select(DefaultGrant)).all())
    existing = {
        (g.target_type, g.target_id)
        for g in db.scalars(select(GroupGrant).where(GroupGrant.group_id == group.id)).all()
    }
    created = 0
    for default in defaults:
        if (default.target_type, default.target_id) in existing:
            continue
        db.add(
            GroupGrant(
                group_id=group.id,
                target_type=default.target_type,
                target_id=default.target_id,
                added_by_user_id=actor.id,
            )
        )
        created += 1
    if created:
        db.flush()
    return created


def delete_personal_group(db: Session, *, user_id: uuid.UUID, actor: User) -> None:
    """Delete the personal group for a user (memberships/grants cascade). Writes an audit event."""
    if not _groups_table_present(db):
        return
    group = db.scalar(select(Group).where(Group.personal_user_id == user_id))
    if group is None:
        return
    group_id = group.id
    db.delete(group)
    db.flush()
    record_event(
        db,
        "group.deleted",
        actor_user_id=actor.id,
        entity_type="group",
        entity_id=str(group_id),
        details={"is_personal": True, "personal_user_id": str(user_id)},
    )


# --------------------------------------------------------------------------------------------------
# Admin CRUD — groups
# --------------------------------------------------------------------------------------------------
def list_groups(db: Session) -> list[Group]:
    """Return all groups, personal first then by name."""
    return list(db.scalars(select(Group).order_by(Group.is_personal.desc(), Group.name)).all())


def create_group(db: Session, *, name: str, actor: User) -> Group:
    """Create a shared (non-personal) group. Writes a ``group.created`` audit event."""
    name = (name or "").strip()
    if not name:
        raise GroupError("Group name must not be empty")
    if db.scalar(select(Group).where(Group.name == name)) is not None:
        raise GroupError(f"Group {name!r} already exists")
    group = Group(name=name, is_personal=False)
    db.add(group)
    db.flush()
    record_event(
        db,
        "group.created",
        actor_user_id=actor.id,
        entity_type="group",
        entity_id=str(group.id),
        details={"name": name, "is_personal": False},
    )
    return group


def delete_group(db: Session, *, group_id: uuid.UUID, actor: User) -> None:
    """Delete a shared group (members/grants cascade). Refuses to delete a personal group."""
    group = db.get(Group, group_id)
    if group is None:
        raise LookupError(f"Group {group_id} not found")
    if group.is_personal:
        raise GroupError("Personal groups cannot be deleted directly")
    name = group.name
    db.delete(group)
    db.flush()
    record_event(
        db,
        "group.deleted",
        actor_user_id=actor.id,
        entity_type="group",
        entity_id=str(group_id),
        details={"name": name, "is_personal": False},
    )


# --------------------------------------------------------------------------------------------------
# Admin CRUD — membership
# --------------------------------------------------------------------------------------------------
def list_members(db: Session, *, group_id: uuid.UUID) -> list[User]:
    """Return the users in a group, oldest membership first."""
    if db.get(Group, group_id) is None:
        raise LookupError(f"Group {group_id} not found")
    return list(
        db.scalars(
            select(User)
            .join(GroupMembership, GroupMembership.user_id == User.id)
            .where(GroupMembership.group_id == group_id)
            .order_by(GroupMembership.added_at, User.username)
        ).all()
    )


def add_member(db: Session, *, group_id: uuid.UUID, user_id: uuid.UUID, actor: User) -> None:
    """Add a user to a group (idempotent). Writes a ``group.member_added`` audit event."""
    group = db.get(Group, group_id)
    if group is None:
        raise LookupError(f"Group {group_id} not found")
    if db.get(User, user_id) is None:
        raise LookupError(f"User {user_id} not found")
    if db.get(GroupMembership, {"group_id": group_id, "user_id": user_id}) is not None:
        return
    db.add(GroupMembership(group_id=group_id, user_id=user_id, added_by_user_id=actor.id))
    db.flush()
    record_event(
        db,
        "group.member_added",
        actor_user_id=actor.id,
        entity_type="group",
        entity_id=str(group_id),
        details={"user_id": str(user_id)},
    )


def remove_member(db: Session, *, group_id: uuid.UUID, user_id: uuid.UUID, actor: User) -> None:
    """Remove a user from a group. Refuses to remove the owner of a personal group."""
    group = db.get(Group, group_id)
    if group is None:
        raise LookupError(f"Group {group_id} not found")
    if group.is_personal and group.personal_user_id == user_id:
        raise GroupError("Cannot remove a user from their own personal group")
    link = db.get(GroupMembership, {"group_id": group_id, "user_id": user_id})
    if link is None:
        raise LookupError("Membership not found")
    db.delete(link)
    db.flush()
    record_event(
        db,
        "group.member_removed",
        actor_user_id=actor.id,
        entity_type="group",
        entity_id=str(group_id),
        details={"user_id": str(user_id)},
    )


# --------------------------------------------------------------------------------------------------
# Admin CRUD — grants
# --------------------------------------------------------------------------------------------------
def list_grants(db: Session, *, group_id: uuid.UUID) -> list[GroupGrant]:
    """Return the grants held by a group."""
    if db.get(Group, group_id) is None:
        raise LookupError(f"Group {group_id} not found")
    return list(
        db.scalars(
            select(GroupGrant)
            .where(GroupGrant.group_id == group_id)
            .order_by(GroupGrant.target_type, GroupGrant.created_at)
        ).all()
    )


def add_grant(
    db: Session,
    *,
    group_id: uuid.UUID,
    target_type: str,
    target_id: uuid.UUID,
    actor: User,
) -> GroupGrant:
    """Grant a group access to a rack/shelf target (idempotent). Writes an audit event."""
    if db.get(Group, group_id) is None:
        raise LookupError(f"Group {group_id} not found")
    _check_target(db, target_type, target_id)
    existing = db.scalar(
        select(GroupGrant).where(
            GroupGrant.group_id == group_id,
            GroupGrant.target_type == target_type,
            GroupGrant.target_id == target_id,
        )
    )
    if existing is not None:
        return existing
    grant = GroupGrant(
        group_id=group_id,
        target_type=target_type,
        target_id=target_id,
        added_by_user_id=actor.id,
    )
    db.add(grant)
    db.flush()
    record_event(
        db,
        "group.grant_added",
        actor_user_id=actor.id,
        entity_type="group",
        entity_id=str(group_id),
        details={"target_type": target_type, "target_id": str(target_id)},
    )
    return grant


def remove_grant(db: Session, *, grant_id: uuid.UUID, actor: User) -> None:
    """Revoke a group grant. Writes a ``group.grant_removed`` audit event."""
    grant = db.get(GroupGrant, grant_id)
    if grant is None:
        raise LookupError(f"Grant {grant_id} not found")
    group_id = grant.group_id
    details = {"target_type": grant.target_type, "target_id": str(grant.target_id)}
    db.delete(grant)
    db.flush()
    record_event(
        db,
        "group.grant_removed",
        actor_user_id=actor.id,
        entity_type="group",
        entity_id=str(group_id),
        details=details,
    )


# --------------------------------------------------------------------------------------------------
# Admin CRUD — default grants (applied to every new personal group)
# --------------------------------------------------------------------------------------------------
def list_default_grants(db: Session) -> list[DefaultGrant]:
    """Return the configured default-grant set."""
    return list(
        db.scalars(
            select(DefaultGrant).order_by(DefaultGrant.target_type, DefaultGrant.created_at)
        ).all()
    )


def add_default_grant(
    db: Session, *, target_type: str, target_id: uuid.UUID, actor: User
) -> DefaultGrant:
    """Add a target to the default-grant set (idempotent). Writes an audit event."""
    _check_target(db, target_type, target_id)
    existing = db.scalar(
        select(DefaultGrant).where(
            DefaultGrant.target_type == target_type,
            DefaultGrant.target_id == target_id,
        )
    )
    if existing is not None:
        return existing
    default = DefaultGrant(target_type=target_type, target_id=target_id, added_by_user_id=actor.id)
    db.add(default)
    db.flush()
    record_event(
        db,
        "group.default_grant_added",
        actor_user_id=actor.id,
        entity_type="default_grant",
        entity_id=str(default.id),
        details={"target_type": target_type, "target_id": str(target_id)},
    )
    return default


def remove_default_grant(db: Session, *, default_grant_id: uuid.UUID, actor: User) -> None:
    """Remove a target from the default-grant set. Writes an audit event."""
    default = db.get(DefaultGrant, default_grant_id)
    if default is None:
        raise LookupError(f"Default grant {default_grant_id} not found")
    details = {"target_type": default.target_type, "target_id": str(default.target_id)}
    db.delete(default)
    db.flush()
    record_event(
        db,
        "group.default_grant_removed",
        actor_user_id=actor.id,
        entity_type="default_grant",
        entity_id=str(default_grant_id),
        details=details,
    )
