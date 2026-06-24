"""Tag endpoints."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.core.security import Role
from app.db.session import get_db
from app.models.organization import Rack, Shelf, Tag, TagLink
from app.models.user import User
from app.models.work import Work
from app.utils.normalization import normalize_title

router = APIRouter()
DB_DEP = Depends(get_db)
EDITOR_DEP = Depends(require_roles(Role.OWNER, Role.EDITOR))

TAGGABLE_MODELS = {
    "work": Work,
    "shelf": Shelf,
    "rack": Rack,
}


class TagCreate(BaseModel):
    name: str
    color: str | None = None
    description: str | None = None


class TagLinkCreate(BaseModel):
    entity_type: str
    entity_id: uuid.UUID


class TagRead(BaseModel):
    id: uuid.UUID
    name: str
    normalized_name: str
    color: str | None = None
    description: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


@router.get("", response_model=list[TagRead])
def list_tags(db: Session = DB_DEP) -> list[Tag]:
    """List tags."""
    return list(db.scalars(select(Tag).order_by(Tag.name)).all())


@router.post("", response_model=TagRead, status_code=status.HTTP_201_CREATED)
def create_tag(
    payload: TagCreate,
    db: Session = DB_DEP,
    _: User = EDITOR_DEP,
) -> Tag:
    """Create or return a tag by normalized name."""
    normalized_name = normalize_title(payload.name)
    tag = db.scalar(select(Tag).where(Tag.normalized_name == normalized_name))
    if tag is not None:
        return tag
    tag = Tag(
        name=payload.name,
        normalized_name=normalized_name,
        color=payload.color,
        description=payload.description,
    )
    db.add(tag)
    db.commit()
    db.refresh(tag)
    return tag


@router.post("/{tag_id}/links", status_code=status.HTTP_204_NO_CONTENT)
def add_tag_link(
    tag_id: uuid.UUID,
    payload: TagLinkCreate,
    db: Session = DB_DEP,
    actor: User = EDITOR_DEP,
) -> None:
    """Attach a tag to a work, shelf, or rack."""
    if db.get(Tag, tag_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tag not found")
    model = TAGGABLE_MODELS.get(payload.entity_type)
    if model is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported entity type",
        )
    if db.get(model, payload.entity_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entity not found")
    link = db.get(
        TagLink,
        {
            "tag_id": tag_id,
            "entity_type": payload.entity_type,
            "entity_id": payload.entity_id,
        },
    )
    if link is None:
        db.add(
            TagLink(
                tag_id=tag_id,
                entity_type=payload.entity_type,
                entity_id=payload.entity_id,
                created_by_user_id=actor.id,
            )
        )
        db.commit()
