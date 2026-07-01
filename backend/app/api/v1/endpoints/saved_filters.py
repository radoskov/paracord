"""Per-user saved-filter endpoints (Phase B7).

Any authenticated user manages their OWN saved filters (not role-gated). Every ownership check
filters on ``owner_user_id == actor.id`` so another user's filter is invisible (404, never 403);
creating a duplicate ``(owner, name)`` is a 409.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_authenticated_user
from app.db.session import get_db
from app.models.saved_filter import SavedFilter
from app.models.user import User
from app.schemas.saved_filter import (
    SavedFilterCreate,
    SavedFilterRead,
    SavedFilterUpdate,
)
from app.services.saved_filters import get_owned_saved_filter, list_saved_filters

router = APIRouter()
DB_DEP = Depends(get_db)
AUTH_DEP = Depends(require_authenticated_user)


def _name_taken(
    db: Session, actor: User, name: str, *, exclude_id: uuid.UUID | None = None
) -> bool:
    stmt = select(SavedFilter.id).where(
        SavedFilter.owner_user_id == actor.id, SavedFilter.name == name
    )
    if exclude_id is not None:
        stmt = stmt.where(SavedFilter.id != exclude_id)
    return db.scalar(stmt) is not None


@router.get("", response_model=list[SavedFilterRead])
def list_filters(db: Session = DB_DEP, actor: User = AUTH_DEP) -> list[SavedFilter]:
    """List the caller's saved filters (ordered by name)."""
    return list_saved_filters(db, actor)


@router.post("", response_model=SavedFilterRead, status_code=status.HTTP_201_CREATED)
def create_filter(
    payload: SavedFilterCreate, db: Session = DB_DEP, actor: User = AUTH_DEP
) -> SavedFilter:
    """Create a saved filter owned by the caller (409 on a duplicate name)."""
    if _name_taken(db, actor, payload.name):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You already have a saved filter with that name",
        )
    saved = SavedFilter(
        owner_user_id=actor.id,
        name=payload.name,
        search_mode=payload.search_mode,
        query_text=payload.query_text,
        params=payload.params.model_dump(mode="json"),
    )
    db.add(saved)
    db.commit()
    db.refresh(saved)
    return saved


@router.put("/{filter_id}", response_model=SavedFilterRead)
def update_filter(
    filter_id: uuid.UUID,
    payload: SavedFilterUpdate,
    db: Session = DB_DEP,
    actor: User = AUTH_DEP,
) -> SavedFilter:
    """Update the caller's saved filter (404 if it isn't theirs; 409 on a duplicate name)."""
    saved = get_owned_saved_filter(db, actor, filter_id)
    if saved is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Saved filter not found")
    updates = payload.model_dump(exclude_unset=True)
    if "name" in updates and _name_taken(db, actor, updates["name"], exclude_id=filter_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You already have a saved filter with that name",
        )
    if "name" in updates:
        saved.name = updates["name"]
    if "search_mode" in updates:
        saved.search_mode = updates["search_mode"]
    if "query_text" in updates:
        saved.query_text = updates["query_text"]
    if payload.params is not None:
        saved.params = payload.params.model_dump(mode="json")
    db.commit()
    db.refresh(saved)
    return saved


@router.delete("/{filter_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_filter(filter_id: uuid.UUID, db: Session = DB_DEP, actor: User = AUTH_DEP) -> None:
    """Delete the caller's saved filter (404 if it isn't theirs)."""
    saved = get_owned_saved_filter(db, actor, filter_id)
    if saved is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Saved filter not found")
    db.delete(saved)
    db.commit()
