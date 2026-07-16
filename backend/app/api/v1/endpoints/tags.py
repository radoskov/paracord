"""Tag endpoints."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.api.deps import require_contributor, require_min_role
from app.core.security import Role
from app.db.session import get_db
from app.models.organization import (
    Rack,
    RackShelf,
    Shelf,
    ShelfWork,
    Tag,
    TagLink,
    TagRack,
    TagShelf,
)
from app.models.user import User
from app.models.work import Work
from app.services import access
from app.services.audit import record_event
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


class TagUpdate(BaseModel):
    name: str | None = None
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
    # 2026-07-16 tag scoping: the shelves/racks this tag is OFFERED for. Both empty = global.
    shelf_ids: list[uuid.UUID] = []
    rack_ids: list[uuid.UUID] = []

    model_config = {"from_attributes": True}


class TagScopeUpdate(BaseModel):
    """Replace a tag's scope. Empty lists → the tag becomes global (offered everywhere)."""

    shelf_ids: list[uuid.UUID] = []
    rack_ids: list[uuid.UUID] = []


def _tag_reads(db: Session, tags: list[Tag]) -> list[TagRead]:
    """Serialize tags with their scope (batched: two queries, not N)."""
    ids = [t.id for t in tags]
    shelves: dict[uuid.UUID, list[uuid.UUID]] = {}
    racks: dict[uuid.UUID, list[uuid.UUID]] = {}
    if ids:
        for tag_id, shelf_id in db.execute(
            select(TagShelf.tag_id, TagShelf.shelf_id).where(TagShelf.tag_id.in_(ids))
        ).all():
            shelves.setdefault(tag_id, []).append(shelf_id)
        for tag_id, rack_id in db.execute(
            select(TagRack.tag_id, TagRack.rack_id).where(TagRack.tag_id.in_(ids))
        ).all():
            racks.setdefault(tag_id, []).append(rack_id)
    return [
        TagRead(
            id=t.id,
            name=t.name,
            normalized_name=t.normalized_name,
            color=t.color,
            description=t.description,
            created_at=t.created_at,
            shelf_ids=shelves.get(t.id, []),
            rack_ids=racks.get(t.id, []),
        )
        for t in tags
    ]


def _tag_read(db: Session, tag: Tag) -> TagRead:
    return _tag_reads(db, [tag])[0]


@router.get("", response_model=list[TagRead])
def list_tags(
    shelf_id: uuid.UUID | None = Query(default=None),
    rack_id: uuid.UUID | None = Query(default=None),
    db: Session = DB_DEP,
) -> list[TagRead]:
    """List tags. ``shelf_id``/``rack_id`` filter to tags OFFERED there (global tags always shown)."""
    tags = list(db.scalars(select(Tag).order_by(Tag.name)).all())
    reads = _tag_reads(db, tags)
    if shelf_id is None and rack_id is None:
        return reads
    out = []
    for r in reads:
        is_global = not r.shelf_ids and not r.rack_ids
        matches = (shelf_id is not None and shelf_id in r.shelf_ids) or (
            rack_id is not None and rack_id in r.rack_ids
        )
        if is_global or matches:
            out.append(r)
    return out


@router.get("/assignable", response_model=list[TagRead])
def list_assignable_tags(
    work_id: uuid.UUID = Query(),
    db: Session = DB_DEP,
    actor: User = CONTRIBUTOR_DEP,
) -> list[TagRead]:
    """Tags offered for a given paper (2026-07-16): global tags (no scope rows) PLUS tags scoped to
    any shelf the paper is on OR any rack containing one of those shelves."""
    work = db.get(Work, work_id)
    if work is None or not access.can_see_work(db, actor, work):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Work not found")
    shelf_ids = set(
        db.scalars(select(ShelfWork.shelf_id).where(ShelfWork.work_id == work_id)).all()
    )
    rack_ids = (
        set(db.scalars(select(RackShelf.rack_id).where(RackShelf.shelf_id.in_(shelf_ids))).all())
        if shelf_ids
        else set()
    )
    scoped_ids = set(db.scalars(select(TagShelf.tag_id)).all()) | set(
        db.scalars(select(TagRack.tag_id)).all()
    )
    matching = (
        set(db.scalars(select(TagShelf.tag_id).where(TagShelf.shelf_id.in_(shelf_ids))).all())
        if shelf_ids
        else set()
    )
    if rack_ids:
        matching |= set(
            db.scalars(select(TagRack.tag_id).where(TagRack.rack_id.in_(rack_ids))).all()
        )
    tags = list(db.scalars(select(Tag).order_by(Tag.name)).all())
    # A tag is offered when it is global (no scope rows) or its scope matches this paper's places.
    assignable = [t for t in tags if t.id not in scoped_ids or t.id in matching]
    return _tag_reads(db, assignable)


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
        return _tag_read(db, tag)
    tag = Tag(
        name=payload.name,
        normalized_name=normalized_name,
        color=payload.color,
        description=payload.description,
    )
    db.add(tag)
    db.commit()
    db.refresh(tag)
    return _tag_read(db, tag)


@router.patch("/{tag_id}", response_model=TagRead)
def update_tag(
    tag_id: uuid.UUID,
    payload: TagUpdate,
    db: Session = DB_DEP,
    actor: User = CONTRIBUTOR_DEP,
) -> Tag:
    """Rename a tag or edit its colour/description (contributor+, mirrors tag creation).

    Renaming re-derives the normalized name; a rename that would collide with another tag's
    normalized name is rejected (409) rather than silently merging the two tags.
    """
    tag = db.get(Tag, tag_id)
    if tag is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tag not found")
    updates = payload.model_dump(exclude_unset=True)
    if "name" in updates:
        new_name = updates["name"]
        normalized = normalize_title(new_name)
        clash = db.scalar(select(Tag).where(Tag.normalized_name == normalized, Tag.id != tag_id))
        if clash is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A tag with that name already exists",
            )
        tag.name = new_name
        tag.normalized_name = normalized
    if "color" in updates:
        tag.color = updates["color"]
    if "description" in updates:
        tag.description = updates["description"]
    record_event(
        db,
        "tag.modified",
        actor_user_id=actor.id,
        entity_type="tag",
        entity_id=str(tag.id),
        details={"fields": sorted(updates.keys())},
    )
    db.commit()
    db.refresh(tag)
    return _tag_read(db, tag)


@router.put("/{tag_id}/scope", response_model=TagRead)
def set_tag_scope(
    tag_id: uuid.UUID,
    payload: TagScopeUpdate,
    db: Session = DB_DEP,
    actor: User = CONTRIBUTOR_DEP,
) -> TagRead:
    """Replace which shelves/racks a tag is offered for (2026-07-16). Empty lists = global.

    Unknown shelf/rack ids are ignored (kept idempotent); contributor+ like the rest of tag CRUD.
    """
    tag = db.get(Tag, tag_id)
    if tag is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tag not found")
    valid_shelves = set(
        db.scalars(select(Shelf.id).where(Shelf.id.in_(payload.shelf_ids))).all()
    )
    valid_racks = set(db.scalars(select(Rack.id).where(Rack.id.in_(payload.rack_ids))).all())
    db.execute(delete(TagShelf).where(TagShelf.tag_id == tag_id))
    db.execute(delete(TagRack).where(TagRack.tag_id == tag_id))
    db.add_all(TagShelf(tag_id=tag_id, shelf_id=sid) for sid in valid_shelves)
    db.add_all(TagRack(tag_id=tag_id, rack_id=rid) for rid in valid_racks)
    record_event(
        db,
        "tag.scope_set",
        actor_user_id=actor.id,
        entity_type="tag",
        entity_id=str(tag_id),
        details={"shelves": len(valid_shelves), "racks": len(valid_racks)},
    )
    db.commit()
    db.refresh(tag)
    return _tag_read(db, tag)


@router.delete("/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_tag(
    tag_id: uuid.UUID,
    db: Session = DB_DEP,
    actor: User = EDITOR_DEP,
) -> None:
    """Delete a tag and every link to it (editor+, since it detaches the tag across all entities).

    The tagged papers/shelves/racks are untouched — only their ``TagLink`` rows for this tag are
    removed, so they simply lose the tag.
    """
    tag = db.get(Tag, tag_id)
    if tag is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tag not found")
    tag_name = tag.name
    links_removed = (
        db.scalar(select(func.count()).select_from(TagLink).where(TagLink.tag_id == tag_id)) or 0
    )
    db.execute(delete(TagLink).where(TagLink.tag_id == tag_id))
    db.delete(tag)
    record_event(
        db,
        "tag.deleted",
        actor_user_id=actor.id,
        entity_type="tag",
        entity_id=str(tag_id),
        details={"name": tag_name, "links_removed": links_removed},
    )
    db.commit()


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
