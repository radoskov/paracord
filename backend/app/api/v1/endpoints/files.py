"""File and PDF access endpoints."""

import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.core.security import Role
from app.db.session import get_db
from app.models.file import File, Location
from app.models.source import Source
from app.models.user import User
from app.workers.queue import enqueue_extraction

router = APIRouter()
DB_DEP = Depends(get_db)
EDITOR_DEP = Depends(require_roles(Role.OWNER, Role.EDITOR))


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
def stream_file(file_id: uuid.UUID, db: Session = DB_DEP) -> FileResponse:
    """Stream a PDF from a configured server-folder location."""
    file = db.get(File, file_id)
    if file is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    location = db.scalar(
        select(Location)
        .where(
            Location.file_id == file_id,
            Location.location_type == "server_path",
            Location.is_available.is_(True),
        )
        .order_by(Location.is_primary.desc(), Location.created_at.desc())
    )
    if location is None or location.source_id is None or not location.internal_uri:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Streamable PDF not found")

    source = db.get(Source, location.source_id)
    if source is None or source.type != "server_folder" or not source.is_active:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not available")

    path = _validated_server_file_path(source, location.internal_uri)
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="PDF not available")
    return FileResponse(
        path,
        media_type=file.mime_type or "application/pdf",
        filename=file.original_filename or path.name,
    )


def _validated_server_file_path(source: Source, internal_uri: str) -> Path:
    config = source.config or {}
    raw_root = config.get("root_path")
    if not raw_root:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source root not available")
    root = Path(str(raw_root)).expanduser().resolve()
    path = Path(internal_uri).expanduser().resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="File location escapes configured root",
        ) from exc
    return path
