"""Work endpoints."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.core.security import Role
from app.db.session import get_db
from app.models.organization import RackShelf, ShelfWork, TagLink
from app.models.user import User
from app.models.work import Work
from app.utils.normalization import normalize_title

router = APIRouter()
DB_DEP = Depends(get_db)
EDITOR_DEP = Depends(require_roles(Role.OWNER, Role.EDITOR))


class WorkCreate(BaseModel):
    canonical_title: str | None = None
    abstract: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    venue: str | None = None
    year: int | None = None
    reading_status: str = "unread"


class WorkUpdate(BaseModel):
    canonical_title: str | None = None
    abstract: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    venue: str | None = None
    year: int | None = None
    reading_status: str | None = None


class WorkRead(BaseModel):
    id: uuid.UUID
    canonical_title: str | None = None
    abstract: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    venue: str | None = None
    year: int | None = None
    reading_status: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


@router.get("", response_model=list[WorkRead])
def list_works(
    q: str | None = Query(default=None),
    reading_status: str | None = Query(default=None),
    shelf_id: uuid.UUID | None = Query(default=None),
    rack_id: uuid.UUID | None = Query(default=None),
    tag_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = DB_DEP,
) -> list[Work]:
    """List/search works by basic metadata."""
    stmt = select(Work)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                Work.canonical_title.ilike(like),
                Work.doi.ilike(like),
                Work.arxiv_id.ilike(like),
                Work.venue.ilike(like),
            )
        )
    if reading_status:
        stmt = stmt.where(Work.reading_status == reading_status)
    if shelf_id or rack_id:
        stmt = stmt.join(ShelfWork, ShelfWork.work_id == Work.id)
    if shelf_id:
        stmt = stmt.where(ShelfWork.shelf_id == shelf_id)
    if rack_id:
        stmt = (
            stmt.join(RackShelf, RackShelf.shelf_id == ShelfWork.shelf_id)
            .where(RackShelf.rack_id == rack_id)
        )
    if tag_id:
        stmt = stmt.join(
            TagLink,
            (TagLink.entity_id == Work.id) & (TagLink.entity_type == "work"),
        ).where(TagLink.tag_id == tag_id)
    stmt = stmt.distinct().order_by(Work.updated_at.desc()).limit(limit)
    return list(db.scalars(stmt).all())


@router.post("", response_model=WorkRead, status_code=status.HTTP_201_CREATED)
def create_work(
    payload: WorkCreate,
    db: Session = DB_DEP,
    _: User = EDITOR_DEP,
) -> Work:
    """Create a work manually."""
    work = Work(
        canonical_title=payload.canonical_title,
        normalized_title=normalize_title(payload.canonical_title or ""),
        abstract=payload.abstract,
        doi=payload.doi,
        arxiv_id=payload.arxiv_id,
        venue=payload.venue,
        year=payload.year,
        reading_status=payload.reading_status,
        canonical_metadata_source="user",
        user_confirmed=True,
    )
    db.add(work)
    db.commit()
    db.refresh(work)
    return work


@router.get("/{work_id}", response_model=WorkRead)
def get_work(work_id: uuid.UUID, db: Session = DB_DEP) -> Work:
    """Return one work."""
    work = db.get(Work, work_id)
    if work is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Work not found")
    return work


@router.patch("/{work_id}", response_model=WorkRead)
def update_work(
    work_id: uuid.UUID,
    payload: WorkUpdate,
    db: Session = DB_DEP,
    _: User = EDITOR_DEP,
) -> Work:
    """Edit a work manually."""
    work = db.get(Work, work_id)
    if work is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Work not found")
    updates = payload.model_dump(exclude_unset=True)
    for key, value in updates.items():
        setattr(work, key, value)
    if "canonical_title" in updates:
        work.normalized_title = normalize_title(work.canonical_title or "")
    work.updated_at = datetime.utcnow()
    work.user_confirmed = True
    db.commit()
    db.refresh(work)
    return work
