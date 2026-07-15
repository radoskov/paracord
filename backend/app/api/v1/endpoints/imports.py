"""Import pipeline endpoints."""

import json
import logging
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
from app.models.import_staging import ImportStagingBatch, ImportStagingItem
from app.models.source import ImportBatch, Source
from app.models.user import User
from app.schemas.agent import TeleportRequest
from app.services import agent_files, batch_import, import_staging
from app.services.bibliography_import import import_csl, import_ris
from app.services.bibtex import import_bibtex, preview_bibtex
from app.services.identifiers import arxiv_base_id as _arxiv_base_id
from app.services.metadata_enrichment import enrich_work
from app.services.queue_capacity import assert_queue_has_capacity
from app.services.shelf_membership import add_work_to_shelf_checked
from app.services.storage import (
    file_ids_pending_extraction,
    import_server_folder,
    import_uploaded_pdf,
    mark_extraction_requested,
    probe_pdf_openable,
)
from app.utils.normalization import normalize_title
from app.workers.queue import enqueue_extraction, enqueue_staging_extraction

logger = logging.getLogger(__name__)
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
    # False when this import intended to extract file(s) but the extraction queue was unreachable
    # (Redis down), so the jobs were dropped and the recovery sweep will retry (D7). True when the
    # jobs were queued or when the import had nothing to extract (e.g. citation-only imports).
    extraction_queued: bool = True

    model_config = {"from_attributes": True}


@router.post("/folder", response_model=ImportBatchRead, status_code=status.HTTP_201_CREATED)
def import_folder(
    payload: FolderImportCreate,
    db: Session = DB_DEP,
    actor: User = EDITOR_DEP,
) -> ImportBatch:
    """Import PDFs from a configured server-folder source."""
    assert_queue_has_capacity(db)  # D39: reject up front when the processing queue is full
    source = db.get(Source, payload.source_id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")
    try:
        # import_server_folder commits the batch + files itself (D9) and marks each imported file
        # owed an extraction (D7) in that same commit.
        batch = import_server_folder(
            db,
            source=source,
            actor=actor,
            recursive=payload.recursive,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    # Best-effort: queue GROBID extraction for the files just imported (no-op if Redis is down).
    # Report whether every intended job was actually queued so the UI can warn on a dropped enqueue;
    # any drop is recovered by the startup sweep via the owed marker set above.
    pending = file_ids_pending_extraction(db, source.id)
    extraction_queued = True
    for file_id in pending:
        if enqueue_extraction(file_id) is None:
            extraction_queued = False
    batch.extraction_queued = extraction_queued
    return batch


@router.post("/bibtex", response_model=ImportBatchRead, status_code=status.HTTP_201_CREATED)
def import_bibtex_entries(
    payload: BibtexImportCreate,
    db: Session = DB_DEP,
    actor: User = EDITOR_DEP,
) -> ImportBatch:
    """Create works from pasted/uploaded BibTeX content."""
    assert_queue_has_capacity(db)  # D39
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
    assert_queue_has_capacity(db)  # D39
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
    assert_queue_has_capacity(db)  # D39
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
def upload_pdf(
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
    assert_queue_has_capacity(db)  # D39: reject before reading the upload when the queue is full
    if file.content_type and file.content_type not in (
        "application/pdf",
        "application/octet-stream",
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only PDF files are accepted",
        )
    pdf_bytes = file.file.read(_MAX_UPLOAD_BYTES + 1)
    if len(pdf_bytes) > _MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="Uploaded file exceeds 200 MB limit",
        )
    if len(pdf_bytes) < 4 or pdf_bytes[:4] != b"%PDF":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is not a valid PDF",
        )
    pdf_error = probe_pdf_openable(pdf_bytes)  # E2: reject encrypted/unopenable before any worker
    if pdf_error is not None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=pdf_error)
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
    mark_extraction_requested(file_obj)  # owed marker in the same commit (D7)
    db.commit()
    db.refresh(batch)
    batch.extraction_queued = enqueue_extraction(file_obj.id) is not None
    return batch


# ---------------------------------------------------------------------------
# Multi-PDF staging import (batch10 #1): extract before storing records.
# ---------------------------------------------------------------------------


class StagingItemRead(BaseModel):
    id: uuid.UUID
    filename: str
    sha256: str | None = None
    status: str
    error: str | None = None
    # Preview metadata parsed from TEI ({title, authors, year, doi, venue, abstract}).
    parsed: dict | None = None
    # Detected collisions ({same_pdf|same_doi|same_title: [{work_id, title}]}).
    duplicates: dict | None = None
    created_work_id: uuid.UUID | None = None

    model_config = {"from_attributes": True}  # tei_xml is intentionally not exposed


class StagingBatchRead(BaseModel):
    id: uuid.UUID
    mode: str
    status: str
    target_shelf_id: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime
    items: list[StagingItemRead] = []
    # False when extraction jobs could not be queued AND could not run inline (rare).
    extraction_queued: bool = True

    model_config = {"from_attributes": True}


class StagingDecision(BaseModel):
    item_id: uuid.UUID
    action: Literal["accept", "skip"]


class StagingCommitRequest(BaseModel):
    # When True, the server decides (accept every extracted, non-blocked item; skip the rest).
    auto: bool = False
    decisions: list[StagingDecision] = []


class StagingCommitResponse(BaseModel):
    batch_id: uuid.UUID
    created: int
    skipped: int
    created_work_ids: list[uuid.UUID] = []
    warnings: list[str] = []


def _staging_items(db: Session, batch_id: uuid.UUID) -> list[ImportStagingItem]:
    return list(
        db.scalars(
            select(ImportStagingItem)
            .where(ImportStagingItem.batch_id == batch_id)
            .order_by(ImportStagingItem.created_at, ImportStagingItem.id)
        ).all()
    )


def _staging_read(
    db: Session, batch: ImportStagingBatch, *, extraction_queued: bool = True
) -> StagingBatchRead:
    items = [StagingItemRead.model_validate(i) for i in _staging_items(db, batch.id)]
    return StagingBatchRead.model_validate(batch).model_copy(
        update={"items": items, "extraction_queued": extraction_queued}
    )


def _require_own_staging_batch(db: Session, batch_id: uuid.UUID, actor: User) -> ImportStagingBatch:
    from app.services import access

    batch = db.get(ImportStagingBatch, batch_id)
    if batch is None or (
        not access.is_admin_or_owner(actor) and batch.created_by_user_id != actor.id
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Import batch not found")
    return batch


@router.post("/upload-multi", response_model=StagingBatchRead, status_code=status.HTTP_201_CREATED)
def upload_pdfs_multi(
    files: list[UploadFile],
    mode: Literal["preview", "direct"] = Form(default="preview"),
    target_shelf_id: uuid.UUID | None = Form(default=None),
    db: Session = DB_DEP,
    actor: User = EDITOR_DEP,
) -> StagingBatchRead:
    """Upload several PDFs at once; each is extracted **before** any paper record is created.

    Each PDF is stored content-addressed and extracted for preview (title/authors/year/DOI plus
    detected collisions with existing papers). In ``preview`` mode the caller then chooses which to
    create via ``/staging/{id}/commit``; in ``direct`` mode every extracted, non-colliding paper is
    created automatically (a same-PDF or same-DOI collision, or a failed extraction, is skipped with
    a message). A single bad PDF never fails the batch.

    Extraction runs on the background worker; if the queue is unavailable it runs inline so the flow
    still completes. Poll ``GET /staging/{id}`` until ``status == "ready"`` (preview) / ``committed``.
    """
    settings = get_settings()
    assert_queue_has_capacity(db)
    if not files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No files were uploaded"
        )
    if len(files) > import_staging.MAX_STAGING_FILES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"At most {import_staging.MAX_STAGING_FILES} files per batch",
        )
    uploads: list[tuple[str, bytes]] = []
    for upload in files:
        data = upload.file.read(import_staging._MAX_UPLOAD_BYTES + 1)
        uploads.append((upload.filename or "upload.pdf", data))

    batch = import_staging.stage_pdfs(
        db,
        actor=actor,
        uploads=uploads,
        mode=mode,
        target_shelf_id=target_shelf_id,
        settings=settings,
    )
    db.commit()  # persist batch + items so the worker can find them before we enqueue

    # Schedule extraction for each staged item: prefer the worker; fall back to inline when the
    # queue is unavailable (keeps dev/tests and Redis-down deployments working).
    pending = [i for i in _staging_items(db, batch.id) if i.status == "pending"]
    inline_items: list[ImportStagingItem] = []
    for item in pending:
        if enqueue_staging_extraction(item.id) is None:
            inline_items.append(item)
    extraction_queued = True
    if inline_items:
        from app.services.grobid_client import GrobidClient

        client = GrobidClient(settings.grobid_url, settings=settings)
        for item in inline_items:
            import_staging.extract_staging_item(
                db, item=item, fetch_tei=client.process_fulltext_document_sync, settings=settings
            )
        extraction_queued = False

    # If everything finished inline, finalize now; direct mode auto-commits in the same request.
    commit_summary = None
    if import_staging.finalize_if_ready(db, batch) and batch.mode == "direct":
        decisions = import_staging.auto_decisions(_staging_items(db, batch.id))
        commit_summary = import_staging.commit_staging(
            db, actor=actor, batch=batch, decisions=decisions, settings=settings
        )
    db.commit()
    if commit_summary is not None:
        import_staging.enqueue_post_commit_jobs(commit_summary)

    db.refresh(batch)
    return _staging_read(db, batch, extraction_queued=extraction_queued)


@router.get("/staging/{batch_id}", response_model=StagingBatchRead)
def get_staging_batch(
    batch_id: uuid.UUID, db: Session = DB_DEP, actor: User = AUTH_DEP
) -> StagingBatchRead:
    """Return a staging batch and its items — poll this for extraction progress.

    Self-healing (S-batch item 2): each poll re-enqueues items whose extraction job died (worker
    restart mid-job left them parked in pending/extracting forever) and finalizes the batch if the
    last job finished without flipping it — so a batch can no longer wedge in "extracting".
    """
    batch = _require_own_staging_batch(db, batch_id, actor)
    changed = False
    if batch.status == "extracting":
        try:
            import_staging.requeue_stalled_items(db, batch)
        except Exception:  # noqa: BLE001 - self-heal is best-effort; the poll must still answer
            logger.warning("staging requeue failed for batch %s", batch.id, exc_info=True)
        changed = import_staging.finalize_if_ready(db, batch)
    if changed or db.dirty or db.new:
        db.commit()
    return _staging_read(db, batch)


@router.post("/staging/{batch_id}/commit", response_model=StagingCommitResponse)
def commit_staging_batch(
    batch_id: uuid.UUID,
    payload: StagingCommitRequest,
    db: Session = DB_DEP,
    actor: User = CONTRIBUTOR_DEP,
) -> StagingCommitResponse:
    """Create papers for the accepted staged items (or auto-accept all non-blocked ones)."""
    batch = _require_own_staging_batch(db, batch_id, actor)
    if batch.status == "committed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="This batch was already committed"
        )
    if payload.auto and batch.status == "extracting":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Batch is still extracting — auto-commit needs every item processed; "
            "select specific papers to import them now",
        )
    if payload.auto:
        decisions = import_staging.auto_decisions(_staging_items(db, batch.id))
    else:
        decisions = [{"item_id": str(d.item_id), "action": d.action} for d in payload.decisions]
    summary = import_staging.commit_staging(db, actor=actor, batch=batch, decisions=decisions)
    db.commit()
    import_staging.enqueue_post_commit_jobs(summary)
    return StagingCommitResponse(
        batch_id=batch.id,
        created=summary["created"],
        skipped=summary["skipped"],
        created_work_ids=[uuid.UUID(w) for w in summary["created_work_ids"]],
        warnings=summary["warnings"],
    )


class IdentifierImportCreate(BaseModel):
    identifier_type: Literal["arxiv", "doi"]
    value: str
    # Optional import-to-shelf (Phase J item 6).
    target_shelf_id: uuid.UUID | None = None


class IdentifierImportResponse(BaseModel):
    work_id: uuid.UUID
    created: bool
    enriched_sources: list[str]
    # Identifier import creates a metadata-only work with no PDF, so there is no extraction to
    # queue; always True (nothing was dropped). Present for a uniform import-response contract (D7).
    extraction_queued: bool = True


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
    from app.services.default_shelf import place_on_default_if_loose

    assert_queue_has_capacity(db)  # D39
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
    else:
        place_on_default_if_loose(db, work.id, actor_id=actor.id)  # no free-floating papers (#1)
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
    engine: Literal["lookup", "grobid", "bibtex", "identifier"]
    suggested_title: str | None = None
    suggested_authors: list[str] = []
    suggested_year: int | None = None
    suggested_doi: str | None = None
    suggested_venue: str | None = None
    suggested_abstract: str | None = None
    match_status: Literal["matched", "title_only", "no_match"]
    candidates: list[DraftCandidateRead] = []
    # BibTeX-engine extras (None for lookup/grobid drafts).
    suggested_arxiv_id: str | None = None
    suggested_work_type: str | None = None
    existing_work_id: uuid.UUID | None = None


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
    # BibTeX-engine passthrough (kept out of the editable review fields).
    arxiv_id: str | None = None
    work_type: str | None = None


class BatchCommitRequest(BaseModel):
    drafts: list[BatchCommitDraft]
    engine: Literal["lookup", "grobid", "bibtex", "identifier"] = "lookup"
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
        suggested_arxiv_id=draft.suggested_arxiv_id,
        suggested_work_type=draft.suggested_work_type,
        existing_work_id=draft.existing_work_id,
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


class BibtexPreviewRequest(BaseModel):
    content: str


@router.post("/bibtex/preview", response_model=BatchPreviewResponse)
def bibtex_preview(
    payload: BibtexPreviewRequest,
    db: Session = DB_DEP,
    actor: User = CONTRIBUTOR_DEP,
) -> BatchPreviewResponse:
    """Parse BibTeX into reviewable drafts (NO writes) for the preview-&-choose flow.

    Commits go through ``POST /batch/commit`` with ``engine="bibtex"``, so the review UI is shared
    with the batch citation import. Entries already in the library carry ``existing_work_id``.
    """
    _ = actor  # contributor floor matches the /batch/commit path this preview feeds
    if not payload.content.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Empty BibTeX content")
    drafts = preview_bibtex(db, payload.content)
    return BatchPreviewResponse(drafts=[_draft_to_read(d) for d in drafts])


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
            arxiv_id=d.arxiv_id,
            work_type=d.work_type,
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
def get_import_batch(
    batch_id: uuid.UUID,
    db: Session = DB_DEP,
    actor: User = AUTH_DEP,
) -> ImportBatch:
    """Return import batch status and stats (owner/admin see all; others only their own)."""
    from app.services import access

    batch = db.get(ImportBatch, batch_id)
    if batch is None or (
        not access.is_admin_or_owner(actor) and batch.created_by_user_id != actor.id
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Import batch not found")
    return batch
