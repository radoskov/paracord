"""Row endpoints. A Row is the broadest grouping layer — it contains racks (Row ⊃ Rack ⊃ Shelf ⊃
Paper). Mirrors ``racks.py`` one hop up: manage rows and which racks belong to them."""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, field_validator
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.api.deps import require_authenticated_user, require_librarian
from app.db.session import get_db
from app.models.access_settings import ACCESS_LEVELS
from app.models.organization import Rack, RackShelf, Row, RowRack
from app.models.user import User
from app.services import access
from app.services.access_settings import get_default_access_level
from app.services.audit import record_event

router = APIRouter()
DB_DEP = Depends(get_db)
AUTH_DEP = Depends(require_authenticated_user)
# Row structure changes require the librarian floor; per-object grant checks (visible/private need
# a grant) are enforced in the body via ``access.can_modify_row``.
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


class RowCreate(BaseModel):
    name: str
    description: str | None = None
    access_level: str | None = None


class RowUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    status: str | None = None
    access_level: str | None = None

    @field_validator("access_level")
    @classmethod
    def _check_level(cls, value: str | None) -> str | None:
        return _validate_access_level(value)


class RowRead(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None = None
    status: str
    access_level: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class RowRackRead(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None = None
    status: str
    access_level: str

    model_config = {"from_attributes": True}


class RowRackAdd(BaseModel):
    rack_id: uuid.UUID
    position: int | None = None


def _guard_modify_row(db: Session, actor: User, row: Row) -> None:
    if not access.can_modify_row(db, actor, row):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to modify this row",
        )


@router.get("", response_model=list[RowRead])
def list_rows(db: Session = DB_DEP, actor: User = AUTH_DEP) -> list[Row]:
    """List rows the caller may see."""
    stmt = access.visible_rows_query(db, actor).order_by(Row.name)
    return list(db.scalars(stmt).all())


@router.post("", response_model=RowRead, status_code=status.HTTP_201_CREATED)
def create_row(
    payload: RowCreate,
    db: Session = DB_DEP,
    actor: User = LIBRARIAN_DEP,
) -> Row:
    """Create a row (librarian+). Defaults to the global default access level."""
    level = _validate_access_level(payload.access_level) or get_default_access_level(db)
    row = Row(
        name=payload.name,
        description=payload.description,
        access_level=level,
        created_by_user_id=actor.id,
    )
    db.add(row)
    db.flush()
    record_event(
        db,
        "row.created",
        actor_user_id=actor.id,
        entity_type="row",
        entity_id=str(row.id),
        details={"name": row.name, "access_level": row.access_level},
    )
    db.commit()
    db.refresh(row)
    return row


@router.patch("/{row_id}", response_model=RowRead)
def update_row(
    row_id: uuid.UUID,
    payload: RowUpdate,
    db: Session = DB_DEP,
    actor: User = LIBRARIAN_DEP,
) -> Row:
    """Edit or archive a row (requires modify access to this row)."""
    row = db.get(Row, row_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Row not found")
    _guard_modify_row(db, actor, row)
    updates = payload.model_dump(exclude_unset=True)
    for key, value in updates.items():
        setattr(row, key, value)
    row.updated_at = datetime.now(UTC)
    record_event(
        db,
        "row.modified",
        actor_user_id=actor.id,
        entity_type="row",
        entity_id=str(row.id),
        details={"fields": sorted(updates.keys())},
    )
    db.commit()
    db.refresh(row)
    return row


@router.get("/{row_id}/racks", response_model=list[RowRackRead])
def list_row_racks(
    row_id: uuid.UUID, db: Session = DB_DEP, actor: User = AUTH_DEP
) -> list[Rack]:
    """List racks in a row (filtered to racks the caller may see)."""
    row = db.get(Row, row_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Row not found")
    if not access.can_see_row(db, actor, row):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Row not found")
    stmt = (
        access.visible_racks_query(db, actor)
        .join(RowRack, RowRack.rack_id == Rack.id)
        .where(RowRack.row_id == row_id)
        .order_by(RowRack.position.nullslast(), Rack.name)
    )
    return list(db.scalars(stmt).all())


@router.post("/{row_id}/racks", status_code=status.HTTP_204_NO_CONTENT)
def add_rack_to_row(
    row_id: uuid.UUID,
    payload: RowRackAdd,
    db: Session = DB_DEP,
    actor: User = LIBRARIAN_DEP,
) -> None:
    """Add a rack to a row (requires modify access to both row and rack)."""
    row = db.get(Row, row_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Row not found")
    _guard_modify_row(db, actor, row)
    rack = db.get(Rack, payload.rack_id)
    if rack is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Rack not found")
    if not access.can_modify_rack(db, actor, rack):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to modify this rack",
        )
    # RowRack's primary key is the (row_id, rack_id) pair, so a composite-key lookup takes a dict.
    link = db.get(RowRack, {"row_id": row_id, "rack_id": payload.rack_id})
    if link is None:
        db.add(
            RowRack(
                row_id=row_id,
                rack_id=payload.rack_id,
                added_by_user_id=actor.id,
                position=payload.position,
            )
        )
    else:
        link.position = payload.position
    db.commit()


@router.delete("/{row_id}/racks/{rack_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_rack_from_row(
    row_id: uuid.UUID,
    rack_id: uuid.UUID,
    db: Session = DB_DEP,
    actor: User = LIBRARIAN_DEP,
) -> None:
    """Remove a rack from a row (requires modify access to the row)."""
    row = db.get(Row, row_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Row rack not found")
    _guard_modify_row(db, actor, row)
    link = db.get(RowRack, {"row_id": row_id, "rack_id": rack_id})
    if link is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Row rack not found")
    db.delete(link)
    db.commit()


@router.delete("/{row_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_row(
    row_id: uuid.UUID,
    delete_racks: bool = Query(default=False),
    db: Session = DB_DEP,
    actor: User = LIBRARIAN_DEP,
) -> None:
    """Hard-delete a row (requires modify access).

    ``delete_racks=false`` (default): the row and its row↔rack links are removed; the racks
    themselves survive (they simply leave this row — a rack with no row is fine).
    ``delete_racks=true``: each associated rack the caller may modify is ALSO hard-deleted (its
    rack↔shelf links dropped; the shelves survive). Distinct from archiving (PATCH status).
    """
    row = db.get(Row, row_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Row not found")
    _guard_modify_row(db, actor, row)

    if delete_racks:
        rack_ids = list(db.scalars(select(RowRack.rack_id).where(RowRack.row_id == row_id)))
        for rack_id in rack_ids:
            rack = db.get(Rack, rack_id)
            if rack is None or not access.can_modify_rack(db, actor, rack):
                continue  # racks the caller can't modify are just un-rowed below
            db.execute(delete(RackShelf).where(RackShelf.rack_id == rack_id))
            record_event(
                db,
                "rack.deleted",
                actor_user_id=actor.id,
                entity_type="rack",
                entity_id=str(rack_id),
                details={"via": "row.delete"},
            )
            db.delete(rack)  # RowRack links to this rack CASCADE away

    # Drop any remaining row↔rack links (kept/skipped racks) and the row itself.
    db.execute(delete(RowRack).where(RowRack.row_id == row_id))
    record_event(
        db,
        "row.deleted",
        actor_user_id=actor.id,
        entity_type="row",
        entity_id=str(row_id),
        details={"name": row.name, "delete_racks": delete_racks},
    )
    db.delete(row)
    db.commit()
