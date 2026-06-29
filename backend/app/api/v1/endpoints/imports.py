"""Import pipeline endpoints."""

import json
import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_roles
from app.core.config import get_settings
from app.core.security import Role
from app.db.session import get_db
from app.models.source import ImportBatch, Source
from app.models.user import User
from app.schemas.agent import TeleportRequest
from app.services import agent_files
from app.services.bibliography_import import import_csl, import_ris
from app.services.bibtex import import_bibtex
from app.services.identifiers import arxiv_base_id as _arxiv_base_id
from app.services.metadata_enrichment import enrich_work
from app.services.storage import (
    file_ids_pending_extraction,
    import_server_folder,
    import_uploaded_pdf,
)
from app.utils.normalization import normalize_title
from app.workers.queue import enqueue_extraction

router = APIRouter()
DB_DEP = Depends(get_db)
EDITOR_DEP = Depends(require_roles(Role.OWNER, Role.EDITOR))


class FolderImportCreate(BaseModel):
    source_id: uuid.UUID
    recursive: bool = True


class BibtexImportCreate(BaseModel):
    content: str


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


@router.post("/bibtex", response_model=ImportBatchRead, status_code=status.HTTP_201_CREATED)
def import_bibtex_entries(
    payload: BibtexImportCreate,
    db: Session = DB_DEP,
    actor: User = EDITOR_DEP,
) -> ImportBatch:
    """Create works from pasted/uploaded BibTeX content."""
    if not payload.content.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Empty BibTeX content")
    batch = import_bibtex(db, payload.content, actor=actor)
    db.commit()
    db.refresh(batch)
    return batch


@router.post("/ris", response_model=ImportBatchRead, status_code=status.HTTP_201_CREATED)
def import_ris_entries(
    payload: BibtexImportCreate,
    db: Session = DB_DEP,
    actor: User = EDITOR_DEP,
) -> ImportBatch:
    """Create works from pasted/uploaded RIS content."""
    if not payload.content.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Empty RIS content")
    batch = import_ris(db, payload.content, actor=actor)
    db.commit()
    db.refresh(batch)
    return batch


@router.post("/csl", response_model=ImportBatchRead, status_code=status.HTTP_201_CREATED)
def import_csl_entries(
    payload: BibtexImportCreate,
    db: Session = DB_DEP,
    actor: User = EDITOR_DEP,
) -> ImportBatch:
    """Create works from pasted/uploaded CSL-JSON content."""
    if not payload.content.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Empty CSL content")
    try:
        batch = import_csl(db, payload.content, actor=actor)
    except (ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"Invalid CSL JSON: {exc}"
        ) from exc
    db.commit()
    db.refresh(batch)
    return batch


@router.post("/teleport", status_code=status.HTTP_202_ACCEPTED)
def request_teleport(
    payload: TeleportRequest,
    db: Session = DB_DEP,
    actor: User = EDITOR_DEP,
) -> dict[str, str]:
    """Request a teleport of an agent-indexed file to the managed library (user-authorised).

    Marks the manifest entry as requested; the agent then pushes the bytes to
    ``/agents/teleports/{local_file_id}/content``.
    """
    try:
        agent_files.request_teleport(
            db,
            agent_id=payload.agent_id,
            local_file_id=payload.local_file_id,
            requested_by=actor,
        )
    except ValueError as exc:
        code = status.HTTP_404_NOT_FOUND if "No such" in str(exc) else status.HTTP_409_CONFLICT
        raise HTTPException(status_code=code, detail=str(exc)) from exc
    db.commit()
    return {
        "agent_id": str(payload.agent_id),
        "local_file_id": payload.local_file_id,
        "status": "requested",
    }


_MAX_UPLOAD_BYTES = 200 * 1024 * 1024  # 200 MB hard limit


@router.post("/upload", response_model=ImportBatchRead, status_code=status.HTTP_201_CREATED)
async def upload_pdf(
    file: UploadFile,
    db: Session = DB_DEP,
    actor: User = EDITOR_DEP,
) -> ImportBatch:
    """Upload a single PDF to the managed library (content-addressed, auto-deduped)."""
    if file.content_type and file.content_type not in (
        "application/pdf",
        "application/octet-stream",
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only PDF files are accepted",
        )
    pdf_bytes = await file.read(_MAX_UPLOAD_BYTES + 1)
    if len(pdf_bytes) > _MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Uploaded file exceeds 200 MB limit",
        )
    if len(pdf_bytes) < 4 or pdf_bytes[:4] != b"%PDF":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is not a valid PDF",
        )
    try:
        batch, file_obj, _created = import_uploaded_pdf(
            db,
            filename=file.filename or "upload.pdf",
            pdf_bytes=pdf_bytes,
            actor=actor,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    db.commit()
    db.refresh(batch)
    enqueue_extraction(file_obj.id)
    return batch


class IdentifierImportCreate(BaseModel):
    identifier_type: Literal["arxiv", "doi"]
    value: str


class IdentifierImportResponse(BaseModel):
    work_id: uuid.UUID
    created: bool
    enriched_sources: list[str]


@router.post(
    "/identifier",
    response_model=IdentifierImportResponse,
    status_code=status.HTTP_201_CREATED,
)
def import_by_identifier(
    payload: IdentifierImportCreate,
    db: Session = DB_DEP,
    actor: User = EDITOR_DEP,
) -> IdentifierImportResponse:
    """Create or locate a work from an arXiv id or DOI, then enrich from external sources.

    Returns the work id and a flag indicating whether a new work was created.  If a work
    with the same identifier already exists it is returned unchanged (idempotent).
    """
    from app.models.work import Work
    from app.services.audit import record_event

    value = payload.value.strip()
    if not value:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Empty identifier")

    if payload.identifier_type == "arxiv":
        base = _arxiv_base_id(value)
        if not base:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid arXiv identifier"
            )
        existing = db.scalar(select(Work).where(Work.arxiv_base_id == base))
        if existing is not None:
            settings = get_settings()
            result = enrich_work(db, existing, settings=settings, actor_user_id=actor.id)
            db.commit()
            return IdentifierImportResponse(
                work_id=existing.id, created=False, enriched_sources=result["sources"]
            )
        work = Work(
            canonical_title=f"arXiv:{base}",
            normalized_title=normalize_title(f"arxiv {base}"),
            canonical_metadata_source="identifier",
            arxiv_id=value,
            arxiv_base_id=base,
        )
    else:
        doi = value.removeprefix("https://doi.org/").removeprefix("http://doi.org/").strip()
        if not doi:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid DOI")
        existing = db.scalar(select(Work).where(Work.doi == doi))
        if existing is not None:
            settings = get_settings()
            result = enrich_work(db, existing, settings=settings, actor_user_id=actor.id)
            db.commit()
            return IdentifierImportResponse(
                work_id=existing.id, created=False, enriched_sources=result["sources"]
            )
        work = Work(
            canonical_title=f"DOI:{doi}",
            normalized_title=normalize_title(f"doi {doi}"),
            canonical_metadata_source="identifier",
            doi=doi,
        )

    db.add(work)
    db.flush()
    record_event(
        db,
        "import.identifier",
        actor_user_id=actor.id,
        entity_type="work",
        entity_id=str(work.id),
        details={"identifier_type": payload.identifier_type, "value": value},
    )
    settings = get_settings()
    result = enrich_work(db, work, settings=settings, actor_user_id=actor.id)
    db.commit()
    return IdentifierImportResponse(
        work_id=work.id, created=True, enriched_sources=result["sources"]
    )


@router.get("/{batch_id}", response_model=ImportBatchRead)
def get_import_batch(batch_id: uuid.UUID, db: Session = DB_DEP) -> ImportBatch:
    """Return import batch status and stats."""
    batch = db.get(ImportBatch, batch_id)
    if batch is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Import batch not found")
    return batch
