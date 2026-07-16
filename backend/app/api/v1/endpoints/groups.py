"""Admin API for user groups, grants, default grants and access settings (Phase H).

All routes are mounted under ``/api/v1/admin`` and gated by ``require_admin`` (owner or admin).
Personal groups are listed but cannot be deleted directly (they follow user lifecycle).
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.db.session import get_db
from app.models.access_settings import ACCESS_LEVELS
from app.models.user import User
from app.schemas.group import (
    AccessSettingsOut,
    AccessSettingsUpdate,
    DefaultGrantAdd,
    DefaultGrantOut,
    GrantAdd,
    GrantOut,
    GroupCreate,
    GroupMemberOut,
    GroupOut,
    MembershipAdd,
)
from app.services import groups as groups_service
from app.services.access_settings import get_default_access_level, set_default_access_level

router = APIRouter()
DB_DEP = Depends(get_db)
ADMIN_DEP = Depends(require_admin)


def _map_group_error(exc: groups_service.GroupError) -> HTTPException:
    """Translate a service-level validation error (e.g. duplicate name) into a 400 response."""
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


# --------------------------------------------------------------------------------------------------
# Groups
# --------------------------------------------------------------------------------------------------
@router.get("/groups", response_model=list[GroupOut])
def list_groups(db: Session = DB_DEP, _: User = ADMIN_DEP) -> list:
    """List all groups (personal first)."""
    return groups_service.list_groups(db)


@router.post("/groups", response_model=GroupOut, status_code=status.HTTP_201_CREATED)
def create_group(payload: GroupCreate, db: Session = DB_DEP, actor: User = ADMIN_DEP) -> object:
    """Create a shared group."""
    try:
        group = groups_service.create_group(db, name=payload.name, actor=actor)
    except groups_service.GroupError as exc:
        raise _map_group_error(exc) from exc
    db.commit()
    db.refresh(group)
    return group


@router.delete("/groups/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_group(group_id: uuid.UUID, db: Session = DB_DEP, actor: User = ADMIN_DEP) -> None:
    """Delete a shared group (refuses personal groups)."""
    try:
        groups_service.delete_group(db, group_id=group_id, actor=actor)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except groups_service.GroupError as exc:
        raise _map_group_error(exc) from exc
    db.commit()


# --------------------------------------------------------------------------------------------------
# Membership
# --------------------------------------------------------------------------------------------------
@router.get("/groups/{group_id}/members", response_model=list[GroupMemberOut])
def list_members(group_id: uuid.UUID, db: Session = DB_DEP, _: User = ADMIN_DEP) -> list:
    """List members of a group."""
    try:
        return groups_service.list_members(db, group_id=group_id)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/groups/{group_id}/members", status_code=status.HTTP_204_NO_CONTENT)
def add_member(
    group_id: uuid.UUID, payload: MembershipAdd, db: Session = DB_DEP, actor: User = ADMIN_DEP
) -> None:
    """Add a user to a group."""
    try:
        groups_service.add_member(db, group_id=group_id, user_id=payload.user_id, actor=actor)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    db.commit()


@router.delete("/groups/{group_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_member(
    group_id: uuid.UUID, user_id: uuid.UUID, db: Session = DB_DEP, actor: User = ADMIN_DEP
) -> None:
    """Remove a user from a group (refuses the owner of a personal group)."""
    try:
        groups_service.remove_member(db, group_id=group_id, user_id=user_id, actor=actor)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except groups_service.GroupError as exc:
        raise _map_group_error(exc) from exc
    db.commit()


# --------------------------------------------------------------------------------------------------
# Grants
# --------------------------------------------------------------------------------------------------
@router.get("/groups/{group_id}/grants", response_model=list[GrantOut])
def list_grants(group_id: uuid.UUID, db: Session = DB_DEP, _: User = ADMIN_DEP) -> list:
    """List grants held by a group."""
    try:
        return groups_service.list_grants(db, group_id=group_id)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post(
    "/groups/{group_id}/grants", response_model=GrantOut, status_code=status.HTTP_201_CREATED
)
def add_grant(
    group_id: uuid.UUID, payload: GrantAdd, db: Session = DB_DEP, actor: User = ADMIN_DEP
) -> object:
    """Grant a group access to a rack/shelf target."""
    try:
        grant = groups_service.add_grant(
            db,
            group_id=group_id,
            target_type=payload.target_type,
            target_id=payload.target_id,
            actor=actor,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except groups_service.GroupError as exc:
        raise _map_group_error(exc) from exc
    db.commit()
    db.refresh(grant)
    return grant


@router.delete("/grants/{grant_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_grant(grant_id: uuid.UUID, db: Session = DB_DEP, actor: User = ADMIN_DEP) -> None:
    """Revoke a group grant."""
    try:
        groups_service.remove_grant(db, grant_id=grant_id, actor=actor)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    db.commit()


# --------------------------------------------------------------------------------------------------
# Default grants (applied to every new personal group)
# --------------------------------------------------------------------------------------------------
@router.get("/default-grants", response_model=list[DefaultGrantOut])
def list_default_grants(db: Session = DB_DEP, _: User = ADMIN_DEP) -> list:
    """List the default-grant set."""
    return groups_service.list_default_grants(db)


@router.post("/default-grants", response_model=DefaultGrantOut, status_code=status.HTTP_201_CREATED)
def add_default_grant(
    payload: DefaultGrantAdd, db: Session = DB_DEP, actor: User = ADMIN_DEP
) -> object:
    """Add a target to the default-grant set."""
    try:
        default = groups_service.add_default_grant(
            db, target_type=payload.target_type, target_id=payload.target_id, actor=actor
        )
    except groups_service.GroupError as exc:
        raise _map_group_error(exc) from exc
    db.commit()
    db.refresh(default)
    return default


@router.delete("/default-grants/{default_grant_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_default_grant(
    default_grant_id: uuid.UUID, db: Session = DB_DEP, actor: User = ADMIN_DEP
) -> None:
    """Remove a target from the default-grant set."""
    try:
        groups_service.remove_default_grant(db, default_grant_id=default_grant_id, actor=actor)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    db.commit()


# --------------------------------------------------------------------------------------------------
# Access settings (global default access level)
# --------------------------------------------------------------------------------------------------
@router.get("/access-settings", response_model=AccessSettingsOut)
def get_access_settings(db: Session = DB_DEP, _: User = ADMIN_DEP) -> AccessSettingsOut:
    """Return the global default access level."""
    return AccessSettingsOut(
        default_access_level=get_default_access_level(db), allowed=list(ACCESS_LEVELS)
    )


@router.put("/access-settings", response_model=AccessSettingsOut)
def update_access_settings(
    payload: AccessSettingsUpdate, db: Session = DB_DEP, actor: User = ADMIN_DEP
) -> AccessSettingsOut:
    """Set the global default access level (applied to newly created racks/shelves)."""
    try:
        level = set_default_access_level(
            db, access_level=payload.default_access_level, actor_user_id=actor.id
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    db.commit()
    return AccessSettingsOut(default_access_level=level, allowed=list(ACCESS_LEVELS))
