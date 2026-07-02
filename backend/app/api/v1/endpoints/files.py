"""File and PDF access endpoints."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.api.deps import require_authenticated_user, require_contributor
from app.core.config import get_settings
from app.db.session import get_db
from app.models.file import File, FileWorkLink
from app.models.user import User
from app.services import access
from app.services import ocr as ocr_service
from app.services.ai_config import get_ai_config
from app.services.audit import record_event
from app.services.file_paths import (
    FileLocationError,
    resolve_backend_readable_pdf_path,
    resolve_streamable_pdf_path,
)
from app.workers.queue import enqueue_extraction

router = APIRouter()
DB_DEP = Depends(get_db)
CONTRIBUTOR_DEP = Depends(require_contributor)
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
    actor: User = AUTH_DEP,
) -> list[File]:
    """List file metadata for the library file view (filtered to files the caller may see)."""
    stmt = select(File).order_by(File.created_at.desc())
    visible = access.visible_work_ids(db, actor)
    if visible is not None:
        # A file is visible when loose (linked to no work) or linked to a visible work.
        linked = select(FileWorkLink.file_id).where(FileWorkLink.file_id == File.id)
        loose = ~linked.exists()
        visible_link = (
            select(FileWorkLink.file_id)
            .where(FileWorkLink.file_id == File.id, FileWorkLink.work_id.in_(visible))
            .exists()
        )
        stmt = stmt.where(or_(loose, visible_link))
    return list(db.scalars(stmt.limit(limit)).all())


@router.get("/{file_id}", response_model=FileRead)
def get_file(file_id: uuid.UUID, db: Session = DB_DEP, actor: User = AUTH_DEP) -> File:
    """Return file metadata and quick preview text."""
    file = db.get(File, file_id)
    if file is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
    if not access.can_see_file(db, actor, file_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
    return file


@router.post("/{file_id}/extract", status_code=status.HTTP_202_ACCEPTED)
def extract_file(
    file_id: uuid.UUID,
    force_ocr: bool = Query(default=False),
    db: Session = DB_DEP,
    actor: User = CONTRIBUTOR_DEP,
) -> dict[str, str | None]:
    """Queue GROBID extraction for a file (runs in the background worker).

    ``force_ocr=true`` re-runs OCRmyPDF even when the text layer looks fine / OCR is disabled —
    the manual "Force OCR" action for a scanned PDF that came out textless (#22)."""
    file = db.get(File, file_id)
    if file is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
    if not access.can_see_file(db, actor, file_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
    # Persist the owed marker before enqueue (D7) so a dropped enqueue is recovered on startup.
    from app.services.storage import mark_extraction_requested

    mark_extraction_requested(file)
    db.commit()
    job_id = enqueue_extraction(file_id, force_ocr=force_ocr)
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
    if actor is not None and not access.can_see_file(db, actor, file_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    try:
        # Prefer the derived searchable-OCR copy (selectable text) when one exists; else the original.
        path = resolve_streamable_pdf_path(db, file=file, settings=get_settings())
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


class FileTextRead(BaseModel):
    text: str
    source: str  # "native" (PDF text layer) | "ocr" (on-the-fly) | "none" (no text)


@router.get("/{file_id}/text", response_model=FileTextRead)
def file_text(
    file_id: uuid.UUID, db: Session = DB_DEP, actor: User | None = AUTH_DEP
) -> FileTextRead:
    """Return the served PDF's plain text (SEE-gated like ``/stream``).

    Uses PyMuPDF's native text layer; when that is sparse (a scanned PDF), it falls back to
    on-the-fly OCR (``get_textpage_ocr``) in the admin-configured OCR language. Powers the reader's
    search / "copy text" fallback for OCR'd or scanned PDFs. Never raises on OCR failure — it
    degrades to whatever native text (possibly empty) was found.
    """
    file = db.get(File, file_id)
    if file is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
    if actor is not None and not access.can_see_file(db, actor, file_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    try:
        path = resolve_backend_readable_pdf_path(db, file=file, settings=get_settings())
    except FileLocationError as exc:
        code = status.HTTP_403_FORBIDDEN if exc.kind == "forbidden" else status.HTTP_404_NOT_FOUND
        raise HTTPException(status_code=code, detail=str(exc)) from exc
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="PDF not available")

    language = get_ai_config(db).ocr_language
    text, source = ocr_service.pymupdf_extract_text(path, language=language)
    return FileTextRead(text=text, source=source)
