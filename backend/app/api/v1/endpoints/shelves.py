"""Shelf endpoints."""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.core.security import Role
from app.db.session import get_db
from app.models.organization import Shelf, ShelfWork
from app.models.user import User
from app.models.work import Work

router = APIRouter()
DB_DEP = Depends(get_db)
EDITOR_DEP = Depends(require_roles(Role.OWNER, Role.EDITOR))


class ShelfCreate(BaseModel):
    name: str
    description: str | None = None


class ShelfUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    status: str | None = None


class ShelfRead(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None = None
    status: str
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


@router.get("", response_model=list[ShelfRead])
def list_shelves(db: Session = DB_DEP) -> list[Shelf]:
    """List active shelves."""
    return list(db.scalars(select(Shelf).order_by(Shelf.name)).all())


@router.post("", response_model=ShelfRead, status_code=status.HTTP_201_CREATED)
def create_shelf(
    payload: ShelfCreate,
    db: Session = DB_DEP,
    actor: User = EDITOR_DEP,
) -> Shelf:
    """Create a shelf."""
    shelf = Shelf(
        name=payload.name,
        description=payload.description,
        created_by_user_id=actor.id,
    )
    db.add(shelf)
    db.commit()
    db.refresh(shelf)
    return shelf


@router.patch("/{shelf_id}", response_model=ShelfRead)
def update_shelf(
    shelf_id: uuid.UUID,
    payload: ShelfUpdate,
    db: Session = DB_DEP,
    _: User = EDITOR_DEP,
) -> Shelf:
    """Edit or archive a shelf."""
    shelf = db.get(Shelf, shelf_id)
    if shelf is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shelf not found")
    updates = payload.model_dump(exclude_unset=True)
    for key, value in updates.items():
        setattr(shelf, key, value)
    shelf.updated_at = datetime.now(UTC)
    db.commit()
    db.refresh(shelf)
    return shelf


@router.get("/{shelf_id}/works", response_model=list[ShelfWorkRead])
def list_shelf_works(shelf_id: uuid.UUID, db: Session = DB_DEP) -> list[Work]:
    """List works in a shelf."""
    if db.get(Shelf, shelf_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shelf not found")
    stmt = (
        select(Work)
        .join(ShelfWork, ShelfWork.work_id == Work.id)
        .where(ShelfWork.shelf_id == shelf_id)
        .order_by(ShelfWork.position.nullslast(), Work.canonical_title)
    )
    return list(db.scalars(stmt).all())


@router.post("/{shelf_id}/works", status_code=status.HTTP_204_NO_CONTENT)
def add_work_to_shelf(
    shelf_id: uuid.UUID,
    payload: ShelfWorkAdd,
    db: Session = DB_DEP,
    actor: User = EDITOR_DEP,
) -> None:
    """Add a work to a shelf."""
    if db.get(Shelf, shelf_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shelf not found")
    if db.get(Work, payload.work_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found")
    link = db.get(ShelfWork, {"shelf_id": shelf_id, "work_id": payload.work_id})
    if link is None:
        db.add(
            ShelfWork(
                shelf_id=shelf_id,
                work_id=payload.work_id,
                added_by_user_id=actor.id,
                position=payload.position,
                note=payload.note,
            )
        )
    else:
        link.position = payload.position
        link.note = payload.note
    db.commit()


@router.delete("/{shelf_id}/works/{work_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_work_from_shelf(
    shelf_id: uuid.UUID,
    work_id: uuid.UUID,
    db: Session = DB_DEP,
    _: User = EDITOR_DEP,
) -> None:
    """Remove a work from a shelf."""
    link = db.get(ShelfWork, {"shelf_id": shelf_id, "work_id": work_id})
    if link is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shelf work not found")
    db.delete(link)
    db.commit()
