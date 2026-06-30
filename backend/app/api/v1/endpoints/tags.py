"""Tag endpoints."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_contributor, require_min_role
from app.core.security import Role
from app.db.session import get_db
from app.models.organization import Rack, Shelf, Tag, TagLink
from app.models.user import User
from app.models.work import Work
from app.services import access
from app.utils.normalization import normalize_title

router = APIRouter()
DB_DEP = Depends(get_db)
# Creating a tag is a low-bar content action (contributor+); attaching/detaching a tag is gated
# per-entity in the body (work => modify-work; shelf/rack => modify-shelf/rack).
CONTRIBUTOR_DEP = Depends(require_contributor)
EDITOR_DEP = Depends(require_min_role(Role.EDITOR))
ENTITY_TYPE_QUERY = Query()
ENTITY_ID_QUERY = Query()

TAGGABLE_MODELS = {
    "work": Work,
    "shelf": Shelf,
    "rack": Rack,
}


def _guard_tag_target(db: Session, actor: User, entity_type: str, entity: object) -> None:
    """Raise 403 if the actor may not modify the tagged entity (work/shelf/rack modify rules)."""
    if entity_type == "work":
        allowed = access.can_modify_work(db, actor, entity)
    elif entity_type == "shelf":
        allowed = access.can_modify_shelf(db, actor, entity)
    elif entity_type == "rack":
        allowed = access.can_modify_rack(db, actor, entity)
    else:
        allowed = False
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to tag this entity",
        )


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
    _: User = CONTRIBUTOR_DEP,
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
    actor: User = CONTRIBUTOR_DEP,
) -> None:
    """Attach a tag to a work, shelf, or rack (requires modify access to that entity)."""
    if db.get(Tag, tag_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tag not found")
    model = TAGGABLE_MODELS.get(payload.entity_type)
    if model is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported entity type",
        )
    entity = db.get(model, payload.entity_id)
    if entity is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entity not found")
    _guard_tag_target(db, actor, payload.entity_type, entity)
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


@router.delete("/{tag_id}/links", status_code=status.HTTP_204_NO_CONTENT)
def remove_tag_link(
    tag_id: uuid.UUID,
    entity_type: str = ENTITY_TYPE_QUERY,
    entity_id: uuid.UUID = ENTITY_ID_QUERY,
    db: Session = DB_DEP,
    actor: User = CONTRIBUTOR_DEP,
) -> None:
    """Remove a tag from an entity (requires modify access to that entity)."""
    link = db.get(
        TagLink,
        {
            "tag_id": tag_id,
            "entity_type": entity_type,
            "entity_id": entity_id,
        },
    )
    if link is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tag link not found")
    model = TAGGABLE_MODELS.get(entity_type)
    if model is not None:
        entity = db.get(model, entity_id)
        if entity is not None:
            _guard_tag_target(db, actor, entity_type, entity)
    db.delete(link)
    db.commit()
