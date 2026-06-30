"""File and PDF access endpoints."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_authenticated_user, require_roles
from app.core.config import get_settings
from app.core.security import Role
from app.db.session import get_db
from app.models.file import File
from app.models.user import User
from app.services.audit import record_event
from app.services.file_paths import FileLocationError, resolve_backend_readable_pdf_path
from app.workers.queue import enqueue_extraction

router = APIRouter()
DB_DEP = Depends(get_db)
EDITOR_DEP = Depends(require_roles(Role.OWNER, Role.EDITOR))
AUTH_DEP = Depends(require_authenticated_user)


class FileRead(BaseModel):
    id: uuid.UUID
    sha256: str
    size_bytes: int
    mime_type: str | None = None
    original_filename: str | None = None
    page_count: int | None = None
    text_layer_quality: str
    status: str
    preview_text: str | None = None
    created_at: datetime
    last_seen_at: datetime | None = None

    model_config = {"from_attributes": True}


@router.get("", response_model=list[FileRead])
def list_files(
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = DB_DEP,
) -> list[File]:
    """List file metadata for the library file view."""
    return list(db.scalars(select(File).order_by(File.created_at.desc()).limit(limit)).all())


@router.get("/{file_id}", response_model=FileRead)
def get_file(file_id: uuid.UUID, db: Session = DB_DEP) -> File:
    """Return file metadata and quick preview text."""
    file = db.get(File, file_id)
    if file is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
    return file


@router.post("/{file_id}/extract", status_code=status.HTTP_202_ACCEPTED)
def extract_file(
    file_id: uuid.UUID,
    db: Session = DB_DEP,
    _: User = EDITOR_DEP,
) -> dict[str, str | None]:
    """Queue GROBID extraction for a file (runs in the background worker)."""
    file = db.get(File, file_id)
    if file is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
    job_id = enqueue_extraction(file_id)
    if job_id is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Extraction queue unavailable",
        )
    return {"job_id": job_id, "status": "queued"}


@router.get("/{file_id}/stream")
def stream_file(
    file_id: uuid.UUID, db: Session = DB_DEP, actor: User | None = AUTH_DEP
) -> FileResponse:
    """Stream a PDF from a server-folder or managed-library location (records a view audit event)."""
    file = db.get(File, file_id)
    if file is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    try:
        path = resolve_backend_readable_pdf_path(db, file=file, settings=get_settings())
    except FileLocationError as exc:
        code = status.HTTP_403_FORBIDDEN if exc.kind == "forbidden" else status.HTTP_404_NOT_FOUND
        raise HTTPException(status_code=code, detail=str(exc)) from exc

    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="PDF not available")

    # §7.6 — record who viewed/downloaded which file.
    record_event(
        db,
        "file.downloaded",
        actor_user_id=actor.id if actor else None,
        entity_type="file",
        entity_id=str(file_id),
    )
    db.commit()
    return FileResponse(
        path,
        media_type=file.mime_type or "application/pdf",
        filename=file.original_filename or path.name,
    )
