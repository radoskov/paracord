"""Shelf endpoints."""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_authenticated_user, require_librarian
from app.db.session import get_db
from app.models.access_settings import ACCESS_LEVELS
from app.models.organization import Shelf, ShelfWork
from app.models.user import User
from app.models.work import Work
from app.services import access
from app.services.access_settings import get_default_access_level
from app.services.audit import record_event
from app.services.default_shelf import (
    get_default_shelf_id,
    hard_delete_shelf,
    place_on_default_if_loose,
)
from app.services.shelf_membership import add_work_to_shelf_checked

router = APIRouter()
DB_DEP = Depends(get_db)
AUTH_DEP = Depends(require_authenticated_user)
# Shelf structure changes (create/edit/membership) require the librarian floor; per-object grant
# checks (visible/private need a grant) are enforced in the body via ``access.can_modify_shelf``.
LIBRARIAN_DEP = Depends(require_librarian)


def _validate_access_level(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized not in ACCESS_LEVELS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown access level (allowed: {ACCESS_LEVELS})",
        )
    return normalized


class ShelfCreate(BaseModel):
    name: str
    description: str | None = None
    access_level: str | None = None


class ShelfUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    status: str | None = None
    access_level: str | None = None

    @field_validator("access_level")
    @classmethod
    def _check_level(cls, value: str | None) -> str | None:
        return _validate_access_level(value)


class ShelfRead(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None = None
    status: str
    access_level: str
    # Whether the requesting caller may modify this shelf's structure/membership (librarian floor +
    # per-shelf grant). Defaulted so existing callers/serializers are unaffected; populated by
    # ``list_shelves`` so the "Put into…" picker can pre-filter to modifiable shelves.
    can_modify: bool = False
    # Whether this is the ephemeral default/Inbox shelf (the fallback home for loose papers). The
    # frontend uses it to exclude the default shelf from "Put into" move-target menus, where moving a
    # paper makes no sense. Defaulted + populated by ``list_shelves``.
    is_default: bool = False
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ShelfWorkRead(BaseModel):
    id: uuid.UUID
    canonical_title: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    venue: str | None = None
    year: int | None = None
    reading_status: str

    model_config = {"from_attributes": True}


class ShelfWorkAdd(BaseModel):
    work_id: uuid.UUID
    position: int | None = None
    note: str | None = None


def _guard_modify_shelf(db: Session, actor: User, shelf: Shelf) -> None:
    if not access.can_modify_shelf(db, actor, shelf):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to modify this shelf",
        )


@router.get("", response_model=list[ShelfRead])
def list_shelves(db: Session = DB_DEP, actor: User = AUTH_DEP) -> list[ShelfRead]:
    """List shelves the caller may see, each annotated with the caller's ``can_modify`` flag."""
    stmt = access.visible_shelves_query(db, actor).order_by(Shelf.name)
    shelves = list(db.scalars(stmt).all())
    # Fetch the caller's shelf grants ONCE (they don't vary per shelf) instead of ~2 queries per
    # shelf inside can_modify_shelf (audit: efficiency #3b).
    granted = (
        set() if access.is_admin_or_owner(actor) else access.granted_target_ids(db, actor, "shelf")
    )
    default_id = get_default_shelf_id(db)
    return [
        ShelfRead.model_validate(shelf).model_copy(
            update={
                "can_modify": access.can_modify_shelf_precomputed(
                    actor, shelf, granted_shelf_ids=granted
                ),
                "is_default": shelf.id == default_id,
            }
        )
        for shelf in shelves
    ]


@router.post("", response_model=ShelfRead, status_code=status.HTTP_201_CREATED)
def create_shelf(
    payload: ShelfCreate,
    db: Session = DB_DEP,
    actor: User = LIBRARIAN_DEP,
) -> Shelf:
    """Create a shelf (librarian+). Defaults to the global default access level."""
    level = _validate_access_level(payload.access_level) or get_default_access_level(db)
    shelf = Shelf(
        name=payload.name,
        description=payload.description,
        access_level=level,
        created_by_user_id=actor.id,
    )
    db.add(shelf)
    db.flush()
    record_event(
        db,
        "shelf.created",
        actor_user_id=actor.id,
        entity_type="shelf",
        entity_id=str(shelf.id),
        details={"name": shelf.name, "access_level": shelf.access_level},
    )
    db.commit()
    db.refresh(shelf)
    return shelf


@router.patch("/{shelf_id}", response_model=ShelfRead)
def update_shelf(
    shelf_id: uuid.UUID,
    payload: ShelfUpdate,
    db: Session = DB_DEP,
    actor: User = LIBRARIAN_DEP,
) -> Shelf:
    """Edit or archive a shelf (requires modify access to this shelf)."""
    shelf = db.get(Shelf, shelf_id)
    if shelf is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shelf not found")
    _guard_modify_shelf(db, actor, shelf)
    updates = payload.model_dump(exclude_unset=True)
    for key, value in updates.items():
        setattr(shelf, key, value)
    shelf.updated_at = datetime.now(UTC)
    record_event(
        db,
        "shelf.modified",
        actor_user_id=actor.id,
        entity_type="shelf",
        entity_id=str(shelf.id),
        details={"fields": sorted(updates.keys())},
    )
    db.commit()
    db.refresh(shelf)
    return shelf


@router.get("/{shelf_id}/works", response_model=list[ShelfWorkRead])
def list_shelf_works(
    shelf_id: uuid.UUID, db: Session = DB_DEP, actor: User = AUTH_DEP
) -> list[Work]:
    """List works in a shelf (requires SEE on the shelf; works the caller can't see are hidden)."""
    shelf = db.get(Shelf, shelf_id)
    if shelf is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shelf not found")
    if not access.can_see_shelf(db, actor, shelf):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shelf not found")
    stmt = (
        select(Work)
        .join(ShelfWork, ShelfWork.work_id == Work.id)
        .where(ShelfWork.shelf_id == shelf_id, Work.merged_into_id.is_(None))
        .order_by(ShelfWork.position.nullslast(), Work.canonical_title)
    )
    visible = access.visible_work_ids(db, actor)
    if visible is not None:
        stmt = stmt.where(Work.id.in_(visible))
    return list(db.scalars(stmt).all())


@router.post("/{shelf_id}/works", status_code=status.HTTP_204_NO_CONTENT)
def add_work_to_shelf(
    shelf_id: uuid.UUID,
    payload: ShelfWorkAdd,
    db: Session = DB_DEP,
    actor: User = LIBRARIAN_DEP,
) -> None:
    """Add a work to a shelf (requires modify access to the shelf)."""
    work = db.get(Work, payload.work_id)
    if work is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found")
    if not access.can_see_work(db, actor, work):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found")
    # The shelf 404 + modify-access (403) check + upsert live in the shared helper so every import
    # path enforces the same rule.
    add_work_to_shelf_checked(
        db,
        shelf_id=shelf_id,
        work_id=payload.work_id,
        actor=actor,
        position=payload.position,
        note=payload.note,
    )
    db.commit()


@router.delete("/{shelf_id}/works/{work_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_work_from_shelf(
    shelf_id: uuid.UUID,
    work_id: uuid.UUID,
    db: Session = DB_DEP,
    actor: User = LIBRARIAN_DEP,
) -> None:
    """Remove a work from a shelf (requires modify access to the shelf)."""
    shelf = db.get(Shelf, shelf_id)
    if shelf is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shelf work not found")
    _guard_modify_shelf(db, actor, shelf)
    link = db.get(ShelfWork, {"shelf_id": shelf_id, "work_id": work_id})
    if link is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shelf work not found")
    db.delete(link)
    db.flush()
    # No free-floating papers (#1): if that was the paper's last real shelf, fall back to default.
    if shelf_id != get_default_shelf_id(db):
        place_on_default_if_loose(db, work_id, actor_id=actor.id)
    db.commit()


@router.delete("/{shelf_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_shelf(
    shelf_id: uuid.UUID,
    db: Session = DB_DEP,
    actor: User = LIBRARIAN_DEP,
) -> None:
    """Hard-delete a shelf (requires modify access). This removes the shelf and its memberships.

    No free-floating papers (#1): a paper that was ONLY on this shelf is moved to the default
    shelf; a paper also on other shelves simply loses this association. The default shelf itself
    cannot be deleted (it is the fallback home). Distinct from archiving (PATCH status), which keeps
    the shelf.
    """
    shelf = db.get(Shelf, shelf_id)
    if shelf is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shelf not found")
    _guard_modify_shelf(db, actor, shelf)
    if shelf_id == get_default_shelf_id(db):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The default shelf cannot be deleted — it is where unshelved papers land.",
        )
    hard_delete_shelf(db, shelf, actor_id=actor.id)
    db.commit()
