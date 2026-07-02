"""Rack endpoints."""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, field_validator
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.api.deps import require_authenticated_user, require_librarian
from app.db.session import get_db
from app.models.access_settings import ACCESS_LEVELS
from app.models.organization import Rack, RackShelf, Shelf
from app.models.user import User
from app.services import access
from app.services.access_settings import get_default_access_level
from app.services.default_shelf import get_default_shelf_id, hard_delete_shelf

router = APIRouter()
DB_DEP = Depends(get_db)
AUTH_DEP = Depends(require_authenticated_user)
# Rack structure changes require the librarian floor; per-object grant checks (visible/private need
# a grant) are enforced in the body via ``access.can_modify_rack``.
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


class RackCreate(BaseModel):
    name: str
    description: str | None = None
    access_level: str | None = None


class RackUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    status: str | None = None
    access_level: str | None = None

    @field_validator("access_level")
    @classmethod
    def _check_level(cls, value: str | None) -> str | None:
        return _validate_access_level(value)


class RackRead(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None = None
    status: str
    access_level: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class RackShelfRead(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None = None
    status: str
    access_level: str

    model_config = {"from_attributes": True}


class RackShelfAdd(BaseModel):
    shelf_id: uuid.UUID
    position: int | None = None


def _guard_modify_rack(db: Session, actor: User, rack: Rack) -> None:
    if not access.can_modify_rack(db, actor, rack):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to modify this rack",
        )


@router.get("", response_model=list[RackRead])
def list_racks(db: Session = DB_DEP, actor: User = AUTH_DEP) -> list[Rack]:
    """List racks the caller may see."""
    stmt = access.visible_racks_query(db, actor).order_by(Rack.name)
    return list(db.scalars(stmt).all())


@router.post("", response_model=RackRead, status_code=status.HTTP_201_CREATED)
def create_rack(
    payload: RackCreate,
    db: Session = DB_DEP,
    actor: User = LIBRARIAN_DEP,
) -> Rack:
    """Create a rack (librarian+). Defaults to the global default access level."""
    level = _validate_access_level(payload.access_level) or get_default_access_level(db)
    rack = Rack(
        name=payload.name,
        description=payload.description,
        access_level=level,
        created_by_user_id=actor.id,
    )
    db.add(rack)
    db.commit()
    db.refresh(rack)
    return rack


@router.patch("/{rack_id}", response_model=RackRead)
def update_rack(
    rack_id: uuid.UUID,
    payload: RackUpdate,
    db: Session = DB_DEP,
    actor: User = LIBRARIAN_DEP,
) -> Rack:
    """Edit or archive a rack (requires modify access to this rack)."""
    rack = db.get(Rack, rack_id)
    if rack is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rack not found")
    _guard_modify_rack(db, actor, rack)
    updates = payload.model_dump(exclude_unset=True)
    for key, value in updates.items():
        setattr(rack, key, value)
    rack.updated_at = datetime.now(UTC)
    db.commit()
    db.refresh(rack)
    return rack


@router.get("/{rack_id}/shelves", response_model=list[RackShelfRead])
def list_rack_shelves(
    rack_id: uuid.UUID, db: Session = DB_DEP, actor: User = AUTH_DEP
) -> list[Shelf]:
    """List shelves in a rack (filtered to shelves the caller may see)."""
    rack = db.get(Rack, rack_id)
    if rack is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rack not found")
    if not access.can_see_rack(db, actor, rack):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rack not found")
    stmt = (
        access.visible_shelves_query(db, actor)
        .join(RackShelf, RackShelf.shelf_id == Shelf.id)
        .where(RackShelf.rack_id == rack_id)
        .order_by(RackShelf.position.nullslast(), Shelf.name)
    )
    return list(db.scalars(stmt).all())


@router.post("/{rack_id}/shelves", status_code=status.HTTP_204_NO_CONTENT)
def add_shelf_to_rack(
    rack_id: uuid.UUID,
    payload: RackShelfAdd,
    db: Session = DB_DEP,
    actor: User = LIBRARIAN_DEP,
) -> None:
    """Add a shelf to a rack (requires modify access to both rack and shelf)."""
    rack = db.get(Rack, rack_id)
    if rack is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rack not found")
    _guard_modify_rack(db, actor, rack)
    shelf = db.get(Shelf, payload.shelf_id)
    if shelf is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shelf not found")
    if not access.can_modify_shelf(db, actor, shelf):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to modify this shelf",
        )
    link = db.get(RackShelf, {"rack_id": rack_id, "shelf_id": payload.shelf_id})
    if link is None:
        db.add(
            RackShelf(
                rack_id=rack_id,
                shelf_id=payload.shelf_id,
                added_by_user_id=actor.id,
                position=payload.position,
            )
        )
    else:
        link.position = payload.position
    db.commit()


@router.delete("/{rack_id}/shelves/{shelf_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_shelf_from_rack(
    rack_id: uuid.UUID,
    shelf_id: uuid.UUID,
    db: Session = DB_DEP,
    actor: User = LIBRARIAN_DEP,
) -> None:
    """Remove a shelf from a rack (requires modify access to the rack)."""
    rack = db.get(Rack, rack_id)
    if rack is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rack shelf not found")
    _guard_modify_rack(db, actor, rack)
    link = db.get(RackShelf, {"rack_id": rack_id, "shelf_id": shelf_id})
    if link is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rack shelf not found")
    db.delete(link)
    db.commit()


@router.delete("/{rack_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_rack(
    rack_id: uuid.UUID,
    delete_shelves: bool = Query(default=False),
    db: Session = DB_DEP,
    actor: User = LIBRARIAN_DEP,
) -> None:
    """Hard-delete a rack (requires modify access).

    ``delete_shelves=false`` (default): the rack and its rack↔shelf links are removed; the shelves
    themselves survive (they simply leave this rack — a shelf with no rack is fine).
    ``delete_shelves=true``: each associated shelf the caller may modify is ALSO hard-deleted (its
    papers left with no shelf fall back to the default shelf), except the default shelf, which is
    never deleted. Distinct from archiving (PATCH status), which keeps the rack.
    """
    rack = db.get(Rack, rack_id)
    if rack is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rack not found")
    _guard_modify_rack(db, actor, rack)

    if delete_shelves:
        default_id = get_default_shelf_id(db)
        shelf_ids = list(db.scalars(select(RackShelf.shelf_id).where(RackShelf.rack_id == rack_id)))
        for shelf_id in shelf_ids:
            if shelf_id == default_id:  # the default shelf is the fallback home; never delete it
                continue
            shelf = db.get(Shelf, shelf_id)
            # Only delete shelves the caller may modify; others are just un-racked below.
            if shelf is not None and access.can_modify_shelf(db, actor, shelf):
                hard_delete_shelf(db, shelf, actor_id=actor.id)

    # Drop any remaining rack↔shelf links (kept/skipped shelves) and the rack itself.
    db.execute(delete(RackShelf).where(RackShelf.rack_id == rack_id))
    db.delete(rack)
    db.commit()
