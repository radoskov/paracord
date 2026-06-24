"""File and PDF access endpoints."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.file import File

router = APIRouter()
DB_DEP = Depends(get_db)


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


@router.get("/{file_id}", response_model=FileRead)
def get_file(file_id: uuid.UUID, db: Session = DB_DEP) -> File:
    """Return file metadata and quick preview text."""
    file = db.get(File, file_id)
    if file is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
    return file
