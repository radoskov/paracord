"""Configured source endpoints."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_min_role
from app.core.config import get_settings
from app.core.security import Role
from app.db.session import get_db
from app.models.source import Source
from app.models.user import User
from app.services.storage import create_server_folder_source

router = APIRouter()
DB_DEP = Depends(get_db)
EDITOR_DEP = Depends(require_min_role(Role.EDITOR))


class ServerFolderSourceCreate(BaseModel):
    name: str
    path_alias: str


class SourceRead(BaseModel):
    id: uuid.UUID
    type: str
    name: str
    path_alias: str | None = None
    is_active: bool

    model_config = {"from_attributes": True}


@router.get("", response_model=list[SourceRead])
def list_sources(db: Session = DB_DEP) -> list[Source]:
    """List configured sources."""
    return list(db.scalars(select(Source).order_by(Source.created_at.desc())).all())


@router.post("/server-folder", response_model=SourceRead, status_code=status.HTTP_201_CREATED)
def add_server_folder_source(
    payload: ServerFolderSourceCreate,
    db: Session = DB_DEP,
    actor: User = EDITOR_DEP,
) -> Source:
    """Create a source for a preconfigured server-folder alias."""
    try:
        source = create_server_folder_source(
            db,
            settings=get_settings(),
            name=payload.name,
            path_alias=payload.path_alias,
            actor=actor,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    db.commit()
    db.refresh(source)
    return source
