"""Import pipeline endpoints."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.core.security import Role
from app.db.session import get_db
from app.models.source import ImportBatch, Source
from app.models.user import User
from app.services.storage import file_ids_pending_extraction, import_server_folder
from app.workers.queue import enqueue_extraction

router = APIRouter()
DB_DEP = Depends(get_db)
EDITOR_DEP = Depends(require_roles(Role.OWNER, Role.EDITOR))


class FolderImportCreate(BaseModel):
    source_id: uuid.UUID
    recursive: bool = True


class ImportBatchRead(BaseModel):
    id: uuid.UUID
    source_id: uuid.UUID | None = None
    input_type: str
    status: str
    stats: dict | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None

    model_config = {"from_attributes": True}


@router.post("/folder", response_model=ImportBatchRead, status_code=status.HTTP_201_CREATED)
def import_folder(
    payload: FolderImportCreate,
    db: Session = DB_DEP,
    actor: User = EDITOR_DEP,
) -> ImportBatch:
    """Import PDFs from a configured server-folder source."""
    source = db.get(Source, payload.source_id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")
    try:
        batch = import_server_folder(
            db,
            source=source,
            actor=actor,
            recursive=payload.recursive,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    db.commit()
    db.refresh(batch)
    # Best-effort: queue GROBID extraction for newly imported files (no-op if Redis is down).
    for file_id in file_ids_pending_extraction(db, source.id):
        enqueue_extraction(file_id)
    return batch


@router.get("/{batch_id}", response_model=ImportBatchRead)
def get_import_batch(batch_id: uuid.UUID, db: Session = DB_DEP) -> ImportBatch:
    """Return import batch status and stats."""
    batch = db.get(ImportBatch, batch_id)
    if batch is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Import batch not found")
    return batch
