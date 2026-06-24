"""Rack endpoints."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.core.security import Role
from app.db.session import get_db
from app.models.organization import Rack, RackShelf, Shelf
from app.models.user import User

router = APIRouter()
DB_DEP = Depends(get_db)
EDITOR_DEP = Depends(require_roles(Role.OWNER, Role.EDITOR))


class RackCreate(BaseModel):
    name: str
    description: str | None = None


class RackUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    status: str | None = None


class RackRead(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None = None
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class RackShelfRead(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None = None
    status: str

    model_config = {"from_attributes": True}


class RackShelfAdd(BaseModel):
    shelf_id: uuid.UUID
    position: int | None = None


@router.get("", response_model=list[RackRead])
def list_racks(db: Session = DB_DEP) -> list[Rack]:
    """List racks."""
    return list(db.scalars(select(Rack).order_by(Rack.name)).all())


@router.post("", response_model=RackRead, status_code=status.HTTP_201_CREATED)
def create_rack(
    payload: RackCreate,
    db: Session = DB_DEP,
    actor: User = EDITOR_DEP,
) -> Rack:
    """Create a rack."""
    rack = Rack(
        name=payload.name,
        description=payload.description,
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
    _: User = EDITOR_DEP,
) -> Rack:
    """Edit or archive a rack."""
    rack = db.get(Rack, rack_id)
    if rack is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rack not found")
    updates = payload.model_dump(exclude_unset=True)
    for key, value in updates.items():
        setattr(rack, key, value)
    rack.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(rack)
    return rack


@router.get("/{rack_id}/shelves", response_model=list[RackShelfRead])
def list_rack_shelves(rack_id: uuid.UUID, db: Session = DB_DEP) -> list[Shelf]:
    """List shelves in a rack."""
    if db.get(Rack, rack_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rack not found")
    stmt = (
        select(Shelf)
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
    actor: User = EDITOR_DEP,
) -> None:
    """Add a shelf to a rack."""
    if db.get(Rack, rack_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rack not found")
    if db.get(Shelf, payload.shelf_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shelf not found")
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
    _: User = EDITOR_DEP,
) -> None:
    """Remove a shelf from a rack."""
    link = db.get(RackShelf, {"rack_id": rack_id, "shelf_id": shelf_id})
    if link is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rack shelf not found")
    db.delete(link)
    db.commit()
