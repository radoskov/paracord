"""Import pipeline endpoints."""

import json
import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import require_authenticated_user, require_contributor, require_min_role
from app.core.config import get_settings
from app.core.security import Role
from app.db.session import get_db
from app.models.agent import Agent
from app.models.source import ImportBatch, Source
from app.models.user import User
from app.schemas.agent import TeleportRequest
from app.services import agent_files, batch_import
from app.services.bibliography_import import import_csl, import_ris
from app.services.bibtex import import_bibtex
from app.services.identifiers import arxiv_base_id as _arxiv_base_id
from app.services.metadata_enrichment import enrich_work
from app.services.shelf_membership import add_work_to_shelf_checked
from app.services.storage import (
    file_ids_pending_extraction,
    import_server_folder,
    import_uploaded_pdf,
)
from app.utils.normalization import normalize_title
from app.workers.queue import enqueue_extraction

router = APIRouter()
DB_DEP = Depends(get_db)
EDITOR_DEP = Depends(require_min_role(Role.EDITOR))
# Paper-creation floor (Phase H/J): contributor+ may import (per-object scoping still applies).
CONTRIBUTOR_DEP = Depends(require_contributor)
# Read-only listing floor: any authenticated user (rows are access-filtered to their own).
AUTH_DEP = Depends(require_authenticated_user)


class FolderImportCreate(BaseModel):
    source_id: uuid.UUID
    recursive: bool = True


class BibtexImportCreate(BaseModel):
    content: str
    # Optional import-to-shelf (Phase J item 6): add every created/matched work to this shelf
    # (404/403 via the shared ACL helper if missing / not modifiable).
    target_shelf_id: uuid.UUID | None = None


class ImportBatchRead(BaseModel):
    id: uuid.UUID
    source_id: uuid.UUID | None = None
    created_by_user_id: uuid.UUID | None = None
    input_type: str
    status: str
    stats: dict | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    # Number of works currently attributed to this batch (Phase B6 picker label).
    work_count: int = 0

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
    batch = import_bibtex(db, payload.content, actor=actor, target_shelf_id=payload.target_shelf_id)
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
    batch = import_ris(db, payload.content, actor=actor, target_shelf_id=payload.target_shelf_id)
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
        batch = import_csl(
            db, payload.content, actor=actor, target_shelf_id=payload.target_shelf_id
        )
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
    agent = db.get(Agent, payload.agent_id)
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    if not agent.can_be_requested:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This agent does not allow teleport requests",
        )
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
    target_shelf_id: uuid.UUID | None = Form(default=None),
    db: Session = DB_DEP,
    actor: User = EDITOR_DEP,
) -> ImportBatch:
    """Upload a single PDF to the managed library (content-addressed, auto-deduped).

    ``target_shelf_id`` (Phase J item 6) optionally adds the uploaded paper to a shelf. The work is
    minted by ``import_uploaded_pdf`` at request time (a new FileWorkLink, or the pre-existing one
    for a deduped upload), so we resolve it and add it through the shared ACL-checked helper *here*
    — synchronous-at-upload rather than the design's worker-deferred option, since the work id is
    already available and we keep the actor's request/ACL context (see report deviation note). A
    missing shelf / lack of modify access (404/403) aborts the upload via the helper.
    """
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
    if target_shelf_id is not None:
        from app.models.file import FileWorkLink

        link = db.scalar(select(FileWorkLink).where(FileWorkLink.file_id == file_obj.id))
        if link is not None:
            # ACL-checked add (404/403 abort the upload before commit) via the shared helper.
            add_work_to_shelf_checked(
                db, shelf_id=target_shelf_id, work_id=link.work_id, actor=actor
            )
    db.commit()
    db.refresh(batch)
    enqueue_extraction(file_obj.id)
    return batch


class IdentifierImportCreate(BaseModel):
    identifier_type: Literal["arxiv", "doi"]
    value: str
    # Optional import-to-shelf (Phase J item 6).
    target_shelf_id: uuid.UUID | None = None


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
            if payload.target_shelf_id is not None:
                add_work_to_shelf_checked(
                    db, shelf_id=payload.target_shelf_id, work_id=existing.id, actor=actor
                )
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
            created_by_user_id=actor.id,
        )
    else:
        doi = value.removeprefix("https://doi.org/").removeprefix("http://doi.org/").strip()
        if not doi:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid DOI")
        existing = db.scalar(select(Work).where(Work.doi == doi))
        if existing is not None:
            settings = get_settings()
            result = enrich_work(db, existing, settings=settings, actor_user_id=actor.id)
            if payload.target_shelf_id is not None:
                add_work_to_shelf_checked(
                    db, shelf_id=payload.target_shelf_id, work_id=existing.id, actor=actor
                )
            db.commit()
            return IdentifierImportResponse(
                work_id=existing.id, created=False, enriched_sources=result["sources"]
            )
        work = Work(
            canonical_title=f"DOI:{doi}",
            normalized_title=normalize_title(f"doi {doi}"),
            canonical_metadata_source="identifier",
            doi=doi,
            created_by_user_id=actor.id,
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
    if payload.target_shelf_id is not None:
        add_work_to_shelf_checked(
            db, shelf_id=payload.target_shelf_id, work_id=work.id, actor=actor
        )
    db.commit()
    return IdentifierImportResponse(
        work_id=work.id, created=True, enriched_sources=result["sources"]
    )


# --- batch citation import (Phase J item 5) --------------------------------


class BatchPreviewRequest(BaseModel):
    # Either pass ``lines`` (already split) or ``text`` (split on newlines). ``lines`` wins.
    lines: list[str] | None = None
    text: str | None = None
    engine: Literal["lookup", "grobid"] = "lookup"

    def resolved_lines(self) -> list[str]:
        if self.lines is not None:
            return self.lines
        if self.text is not None:
            return self.text.splitlines()
        return []


class DraftCandidateRead(BaseModel):
    title: str | None = None
    authors: list[str] = []
    year: int | None = None
    doi: str | None = None
    venue: str | None = None
    source: str
    sources: list[str] = []
    confidence: float


class ParsedDraftRead(BaseModel):
    line_index: int
    raw_line: str
    engine: Literal["lookup", "grobid"]
    suggested_title: str | None = None
    suggested_authors: list[str] = []
    suggested_year: int | None = None
    suggested_doi: str | None = None
    suggested_venue: str | None = None
    suggested_abstract: str | None = None
    match_status: Literal["matched", "title_only", "no_match"]
    candidates: list[DraftCandidateRead] = []


class BatchPreviewResponse(BaseModel):
    drafts: list[ParsedDraftRead]
    degraded: bool = False
    grobid_unavailable: bool = False


class BatchCommitDraft(BaseModel):
    title: str | None = None
    authors: list[str] = []
    year: int | None = None
    doi: str | None = None
    venue: str | None = None
    abstract: str | None = None
    include: bool = True


class BatchCommitRequest(BaseModel):
    drafts: list[BatchCommitDraft]
    engine: Literal["lookup", "grobid"] = "lookup"
    target_shelf_id: uuid.UUID | None = None
    enrich: bool = True


def _draft_to_read(draft: batch_import.ParsedDraft) -> ParsedDraftRead:
    return ParsedDraftRead(
        line_index=draft.line_index,
        raw_line=draft.raw_line,
        engine=draft.engine,
        suggested_title=draft.suggested_title,
        suggested_authors=draft.suggested_authors,
        suggested_year=draft.suggested_year,
        suggested_doi=draft.suggested_doi,
        suggested_venue=draft.suggested_venue,
        suggested_abstract=draft.suggested_abstract,
        match_status=draft.match_status,
        candidates=[
            DraftCandidateRead(
                title=c.title,
                authors=c.authors,
                year=c.year,
                doi=c.doi,
                venue=c.venue,
                source=c.source,
                sources=c.sources,
                confidence=c.confidence,
            )
            for c in draft.candidates
        ],
    )


@router.post("/batch/preview", response_model=BatchPreviewResponse)
def batch_preview(
    payload: BatchPreviewRequest,
    db: Session = DB_DEP,
    actor: User = CONTRIBUTOR_DEP,
) -> BatchPreviewResponse:
    """Preview a batch of raw citation lines (NO writes). Returns 200 even on a partial/degraded run.

    ``engine="lookup"`` searches Crossref/OpenAlex/Semantic Scholar per line (timeboxed by the
    web_find wall-clock budget); ``engine="grobid"`` parses every line in one processCitationList
    call and degrades to title-only (with ``grobid_unavailable``) when GROBID is down.
    """
    _ = db  # no DB access for preview; kept for dependency symmetry
    preview = batch_import.preview_lines(
        payload.resolved_lines(), engine=payload.engine, settings=get_settings()
    )
    return BatchPreviewResponse(
        drafts=[_draft_to_read(d) for d in preview.drafts],
        degraded=preview.degraded,
        grobid_unavailable=preview.grobid_unavailable,
    )


@router.post("/batch/commit", response_model=ImportBatchRead, status_code=status.HTTP_201_CREATED)
def batch_commit(
    payload: BatchCommitRequest,
    db: Session = DB_DEP,
    actor: User = CONTRIBUTOR_DEP,
) -> ImportBatch:
    """Commit confirmed batch drafts into works (deduped) and optionally add them to a shelf.

    Gated at the contributor floor; import-to-shelf additionally requires shelf modify access
    (enforced inside the shared ACL helper — a 404/403 there rolls the whole commit back).
    """
    confirmed = [
        batch_import.ConfirmedDraft(
            title=d.title,
            authors=d.authors,
            year=d.year,
            doi=d.doi,
            venue=d.venue,
            abstract=d.abstract,
            include=d.include,
        )
        for d in payload.drafts
    ]
    batch = batch_import.commit_drafts(
        db,
        confirmed,
        actor=actor,
        engine=payload.engine,
        target_shelf_id=payload.target_shelf_id,
        enrich=payload.enrich,
        settings=get_settings(),
    )
    db.commit()
    db.refresh(batch)
    return batch


@router.get("/batches", response_model=list[ImportBatchRead])
def list_import_batches(
    limit: int = 100,
    db: Session = DB_DEP,
    actor: User = AUTH_DEP,
) -> list[ImportBatchRead]:
    """List import batches (newest first) for the graph's import-batch scope picker.

    Access-filtered (Phase B6/H): owner/admin see all batches; everyone else sees only batches they
    created. Each row carries the current work count for a human-readable picker label.
    """
    from app.models.work import Work
    from app.services import access

    stmt = select(ImportBatch).order_by(ImportBatch.created_at.desc()).limit(limit)
    if not access.is_admin_or_owner(actor):
        stmt = stmt.where(ImportBatch.created_by_user_id == actor.id)
    batches = list(db.scalars(stmt).all())
    counts = dict(
        db.execute(
            select(Work.import_batch_id, func.count(Work.id))
            .where(Work.import_batch_id.in_([b.id for b in batches]))
            .group_by(Work.import_batch_id)
        ).all()
    )
    return [
        ImportBatchRead(
            id=b.id,
            source_id=b.source_id,
            created_by_user_id=b.created_by_user_id,
            input_type=b.input_type,
            status=b.status,
            stats=b.stats,
            created_at=b.created_at,
            started_at=b.started_at,
            finished_at=b.finished_at,
            work_count=counts.get(b.id, 0),
        )
        for b in batches
    ]


@router.get("/{batch_id}", response_model=ImportBatchRead)
def get_import_batch(batch_id: uuid.UUID, db: Session = DB_DEP) -> ImportBatch:
    """Return import batch status and stats."""
    batch = db.get(ImportBatch, batch_id)
    if batch is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Import batch not found")
    return batch
