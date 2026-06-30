"""Work endpoints."""

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.orm import Session

from app.api.deps import require_authenticated_user, require_roles
from app.core.security import Role
from app.db.session import get_db
from app.models.ai import Embedding, Summary
from app.models.annotation import Annotation
from app.models.citation import CitationMention, Reference
from app.models.duplicate import DuplicateCandidate
from app.models.file import File, FileWorkLink
from app.models.metadata import MetadataAssertion
from app.models.organization import RackShelf, ShelfWork, Tag, TagLink
from app.models.user import User
from app.models.work import Work, WorkVersion
from app.services.audit import record_event
from app.services.search_query import parse_search_query
from app.services.semantic_search import related_works
from app.services.storage import attach_uploaded_pdf_to_work
from app.services.summarization import list_work_summaries, summarize_work
from app.utils.normalization import normalize_doi, normalize_title
from app.workers.queue import enqueue_embedding, enqueue_enrichment, enqueue_extraction

_MAX_UPLOAD_BYTES = 200 * 1024 * 1024  # 200 MB hard limit, mirrors /imports/upload

router = APIRouter()
DB_DEP = Depends(get_db)
EDITOR_DEP = Depends(require_roles(Role.OWNER, Role.EDITOR))
AUTH_DEP = Depends(require_authenticated_user)

# Work columns a metadata assertion can be promoted into (mirrors the enrichment service).
_PROMOTABLE_FIELDS = {"title", "abstract", "year", "venue", "doi"}
# Map editable Work attribute names → metadata-assertion field names (for per-field locking).
_WORK_FIELD_TO_ASSERTION = {
    "canonical_title": "title",
    "abstract": "abstract",
    "year": "year",
    "venue": "venue",
    "doi": "doi",
}


class WorkCreate(BaseModel):
    canonical_title: str | None = None
    abstract: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    venue: str | None = None
    year: int | None = None
    reading_status: str = "unread"


class WorkUpdate(BaseModel):
    canonical_title: str | None = None
    abstract: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    venue: str | None = None
    year: int | None = None
    reading_status: str | None = None


class WorkRead(BaseModel):
    id: uuid.UUID
    canonical_title: str | None = None
    abstract: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    venue: str | None = None
    year: int | None = None
    reading_status: str
    canonical_metadata_source: str | None = None
    confirmed_fields: list[str] = []
    keywords: list[str] = []
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# Work columns that the `missing` filter can test for absence (NULL or empty string).
_MISSING_FIELDS = {
    "title": Work.canonical_title,
    "abstract": Work.abstract,
    "year": Work.year,
    "venue": Work.venue,
    "doi": Work.doi,
    "arxiv_id": Work.arxiv_id,
}


@router.get("", response_model=list[WorkRead])
def list_works(
    q: str | None = Query(default=None),
    reading_status: str | None = Query(default=None),
    shelf_id: uuid.UUID | None = Query(default=None),
    rack_id: uuid.UUID | None = Query(default=None),
    tag_id: uuid.UUID | None = Query(default=None),
    has_pdf: bool | None = None,
    has_references: bool | None = None,
    missing: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = DB_DEP,
) -> list[Work]:
    """List/search works by basic metadata and extraction/metadata completeness.

    ``q`` supports structured operators (``author:`` ``year:>=2020`` ``venue:`` ``tag:`` ``type:``
    ``has:pdf`` ``has:references`` ``title:``); the leftover free text matches title/abstract/DOI/
    arXiv/venue. Explicit query params (``has_pdf`` etc.) still work and take precedence.
    """
    parsed = parse_search_query(q)
    stmt = select(Work)
    if parsed.text:
        like = f"%{parsed.text}%"
        stmt = stmt.where(
            or_(
                Work.canonical_title.ilike(like),
                Work.abstract.ilike(like),
                Work.doi.ilike(like),
                Work.arxiv_id.ilike(like),
                Work.venue.ilike(like),
            )
        )
    if parsed.title:
        stmt = stmt.where(Work.canonical_title.ilike(f"%{parsed.title}%"))
    if parsed.venue:
        stmt = stmt.where(Work.venue.ilike(f"%{parsed.venue}%"))
    if parsed.work_type:
        stmt = stmt.where(Work.work_type == parsed.work_type)
    if parsed.year_min is not None:
        stmt = stmt.where(Work.year >= parsed.year_min)
    if parsed.year_max is not None:
        stmt = stmt.where(Work.year <= parsed.year_max)
    if parsed.author:
        author_match = (
            select(MetadataAssertion.id)
            .where(
                MetadataAssertion.entity_type == "work",
                MetadataAssertion.entity_id == Work.id,
                MetadataAssertion.field_name == "authors",
                MetadataAssertion.value.ilike(f"%{parsed.author}%"),
            )
            .exists()
        )
        stmt = stmt.where(author_match)
    if parsed.tag:
        stmt = stmt.where(
            select(Tag.id)
            .join(TagLink, TagLink.tag_id == Tag.id)
            .where(
                TagLink.entity_type == "work",
                TagLink.entity_id == Work.id,
                Tag.name.ilike(f"%{parsed.tag}%"),
            )
            .exists()
        )
    # Operator-derived has:* unless the caller passed explicit query params (those win).
    if has_pdf is None:
        has_pdf = parsed.has_pdf
    if has_references is None:
        has_references = parsed.has_references
    if reading_status:
        stmt = stmt.where(Work.reading_status == reading_status)
    if shelf_id or rack_id:
        stmt = stmt.join(ShelfWork, ShelfWork.work_id == Work.id)
    if shelf_id:
        stmt = stmt.where(ShelfWork.shelf_id == shelf_id)
    if rack_id:
        stmt = stmt.join(RackShelf, RackShelf.shelf_id == ShelfWork.shelf_id).where(
            RackShelf.rack_id == rack_id
        )
    if tag_id:
        stmt = stmt.join(
            TagLink,
            (TagLink.entity_id == Work.id) & (TagLink.entity_type == "work"),
        ).where(TagLink.tag_id == tag_id)
    if has_pdf is not None:
        has_file = select(FileWorkLink.work_id).where(FileWorkLink.work_id == Work.id).exists()
        stmt = stmt.where(has_file if has_pdf else ~has_file)
    if has_references is not None:
        has_refs = select(Reference.id).where(Reference.citing_work_id == Work.id).exists()
        stmt = stmt.where(has_refs if has_references else ~has_refs)
    for field in (missing or "").split(","):
        name = field.strip()
        column = _MISSING_FIELDS.get(name)
        if column is None:
            continue
        # Non-text columns (year) only test NULL; text columns also treat "" as missing.
        stmt = stmt.where(
            column.is_(None) if name == "year" else or_(column.is_(None), column == "")
        )
    stmt = stmt.distinct().order_by(Work.updated_at.desc()).limit(limit)
    return list(db.scalars(stmt).all())


class ReorderQueueRequest(BaseModel):
    work_ids: list[uuid.UUID]


@router.get("/reading-queue", response_model=list[WorkRead])
def reading_queue(db: Session = DB_DEP) -> list[Work]:
    """Return the manual reading queue (status='reading'), ordered by queue_position then recency."""
    stmt = (
        select(Work)
        .where(Work.reading_status == "reading")
        .order_by(Work.queue_position.is_(None), Work.queue_position, Work.updated_at.desc())
    )
    return list(db.scalars(stmt).all())


@router.post("/reading-queue/reorder", response_model=list[WorkRead])
def reorder_reading_queue(
    payload: ReorderQueueRequest, db: Session = DB_DEP, _: User = EDITOR_DEP
) -> list[Work]:
    """Set the reading-queue order to the given work id sequence (SPEC §8.17.1)."""
    for position, work_id in enumerate(payload.work_ids):
        db.execute(update(Work).where(Work.id == work_id).values(queue_position=position))
    db.commit()
    return reading_queue(db=db)


@router.post("", response_model=WorkRead, status_code=status.HTTP_201_CREATED)
def create_work(
    payload: WorkCreate,
    db: Session = DB_DEP,
    _: User = EDITOR_DEP,
) -> Work:
    """Create a work manually."""
    work = Work(
        canonical_title=payload.canonical_title,
        normalized_title=normalize_title(payload.canonical_title or ""),
        abstract=payload.abstract,
        doi=normalize_doi(payload.doi) if payload.doi else None,
        arxiv_id=payload.arxiv_id,
        venue=payload.venue,
        year=payload.year,
        reading_status=payload.reading_status,
        canonical_metadata_source="user",
        user_confirmed=True,
    )
    db.add(work)
    db.commit()
    db.refresh(work)
    enqueue_embedding(work.id)  # index off the search read path (best-effort)
    return work


@router.get("/{work_id}/related", response_model=list[WorkRead])
def related_papers(
    work_id: uuid.UUID, limit: int = Query(default=10, ge=1, le=50), db: Session = DB_DEP
) -> list[Work]:
    """Return papers most similar to this one by embedding neighborhood (SPEC §8.17.2)."""
    work = db.get(Work, work_id)
    if work is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found")
    return [hit.work for hit in related_works(db, work, limit=limit)]


@router.get("/{work_id}", response_model=WorkRead)
def get_work(work_id: uuid.UUID, db: Session = DB_DEP, actor: User = AUTH_DEP) -> Work:
    """Return one work (records a `paper.viewed` audit event, §7.6)."""
    work = db.get(Work, work_id)
    if work is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found")
    record_event(
        db,
        "paper.viewed",
        actor_user_id=actor.id,
        entity_type="work",
        entity_id=str(work_id),
    )
    db.commit()
    return work


@router.patch("/{work_id}", response_model=WorkRead)
def update_work(
    work_id: uuid.UUID,
    payload: WorkUpdate,
    db: Session = DB_DEP,
    _: User = EDITOR_DEP,
) -> Work:
    """Edit a work manually."""
    work = db.get(Work, work_id)
    if work is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found")
    updates = payload.model_dump(exclude_unset=True)
    for key, value in updates.items():
        setattr(work, key, value)
    if "canonical_title" in updates:
        work.normalized_title = normalize_title(work.canonical_title or "")
    work.updated_at = datetime.now(UTC)
    # Lock the specific fields the user edited (SPEC §8.12) so enrichment won't overwrite them.
    edited = {_WORK_FIELD_TO_ASSERTION[k] for k in updates if k in _WORK_FIELD_TO_ASSERTION}
    if edited:
        work.confirmed_fields = sorted(set(work.confirmed_fields or []) | edited)
    db.commit()
    db.refresh(work)
    return work


@router.delete("/{work_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_work(
    work_id: uuid.UUID,
    db: Session = DB_DEP,
    actor: User = EDITOR_DEP,
) -> None:
    """Delete a paper and its dependent rows.

    Removes links and derived data (memberships, tags, assertions, summaries, embeddings,
    references, mentions, annotations, versions, duplicate candidates). The underlying File
    rows and managed PDFs are content-addressed and may be shared, so they are kept; only the
    file↔work links are removed.
    """
    work = db.get(Work, work_id)
    if work is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found")

    db.execute(delete(FileWorkLink).where(FileWorkLink.work_id == work_id))
    db.execute(delete(ShelfWork).where(ShelfWork.work_id == work_id))
    db.execute(delete(Annotation).where(Annotation.work_id == work_id))
    db.execute(delete(CitationMention).where(CitationMention.citing_work_id == work_id))
    db.execute(delete(Reference).where(Reference.citing_work_id == work_id))
    db.execute(delete(WorkVersion).where(WorkVersion.work_id == work_id))
    db.execute(
        delete(MetadataAssertion).where(
            MetadataAssertion.entity_type == "work", MetadataAssertion.entity_id == work_id
        )
    )
    db.execute(delete(Summary).where(Summary.entity_type == "work", Summary.entity_id == work_id))
    db.execute(
        delete(Embedding).where(Embedding.entity_type == "work", Embedding.entity_id == work_id)
    )
    db.execute(delete(TagLink).where(TagLink.entity_type == "work", TagLink.entity_id == work_id))
    db.execute(
        delete(DuplicateCandidate).where(
            or_(
                and_(
                    DuplicateCandidate.entity_a_type == "work",
                    DuplicateCandidate.entity_a_id == work_id,
                ),
                and_(
                    DuplicateCandidate.entity_b_type == "work",
                    DuplicateCandidate.entity_b_id == work_id,
                ),
            )
        )
    )
    # Detach references/mentions in *other* works that resolved to this one.
    db.execute(
        update(Reference)
        .where(Reference.resolved_work_id == work_id)
        .values(resolved_work_id=None, resolution_status="unresolved")
    )
    db.execute(
        update(CitationMention)
        .where(CitationMention.resolved_cited_work_id == work_id)
        .values(resolved_cited_work_id=None)
    )

    db.delete(work)
    record_event(
        db,
        "work.deleted",
        actor_user_id=actor.id,
        entity_type="work",
        entity_id=str(work_id),
    )
    db.commit()


class MetadataAssertionRead(BaseModel):
    id: uuid.UUID
    field_name: str
    value: str
    source: str
    confidence: float | None = None
    selected_as_canonical: bool

    model_config = {"from_attributes": True}


class FieldReview(BaseModel):
    field_name: str
    canonical_value: str | None
    has_conflict: bool
    confirmed: bool = False  # user-locked field (§8.12): enrichment won't overwrite it
    assertions: list[MetadataAssertionRead]


class ConfirmFieldRequest(BaseModel):
    field_name: str
    confirmed: bool = True


class SelectAssertion(BaseModel):
    assertion_id: uuid.UUID


class CitationContextRead(BaseModel):
    id: uuid.UUID
    reference_id: uuid.UUID
    resolved_cited_work_id: uuid.UUID | None = None
    reference_title: str | None = None
    reference_raw_citation: str | None = None
    reference_doi: str | None = None
    marker_text: str | None = None
    section_label: str | None = None
    context_before: str | None = None
    context_sentence: str | None = None
    context_after: str | None = None
    page: int | None = None
    # Full list of PDF coordinate boxes ({"page","x","y","w","h"}); empty if not extracted.
    pdf_coordinates: list[dict] | None = None
    # Convenience scalars for the primary (first) box — what a single-anchor reader uses.
    pdf_x: float | None = None
    pdf_y: float | None = None
    pdf_w: float | None = None
    pdf_h: float | None = None
    source_tei_id: uuid.UUID | None = None


class AnnotationCreate(BaseModel):
    annotation_type: str
    file_id: uuid.UUID | None = None
    version_id: uuid.UUID | None = None
    page: int | None = None
    coordinates: dict | None = None
    selected_text: str | None = None
    content_markdown: str | None = None


class AnnotationRead(BaseModel):
    id: uuid.UUID
    work_id: uuid.UUID
    file_id: uuid.UUID | None = None
    version_id: uuid.UUID | None = None
    page: int | None = None
    coordinates: dict | None = None
    selected_text: str | None = None
    annotation_type: str
    content_markdown: str | None = None
    created_by_user_id: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SummaryCreate(BaseModel):
    summary_type: str = "extractive"
    max_sentences: int = 5
    model_name: str | None = None  # for summary_type=local_llm (Ollama model id)


class SummaryRead(BaseModel):
    id: uuid.UUID
    entity_type: str
    entity_id: uuid.UUID
    summary_type: str
    text: str
    model_name: str | None = None
    prompt_version: str | None = None
    source_sections: list[str] = []
    created_at: datetime

    model_config = {"from_attributes": True}


class ReferenceRead(BaseModel):
    id: uuid.UUID
    title: str | None = None
    raw_citation: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    year: int | None = None
    resolution_status: str
    resolved_work_id: uuid.UUID | None = None

    model_config = {"from_attributes": True}


@router.get("/{work_id}/references", response_model=list[ReferenceRead])
def list_work_references(work_id: uuid.UUID, db: Session = DB_DEP) -> list[Reference]:
    """Return the parsed bibliography (extracted references) for a work."""
    if db.get(Work, work_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found")
    return list(
        db.scalars(
            select(Reference)
            .where(Reference.citing_work_id == work_id)
            .order_by(Reference.created_at)
        ).all()
    )


@router.get("/{work_id}/citation-contexts", response_model=list[CitationContextRead])
def get_work_citation_contexts(
    work_id: uuid.UUID,
    db: Session = DB_DEP,
) -> list[CitationContextRead]:
    """Return in-text citation contexts for one work."""
    if db.get(Work, work_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found")
    rows = db.execute(
        select(CitationMention, Reference)
        .join(Reference, Reference.id == CitationMention.reference_id)
        .where(CitationMention.citing_work_id == work_id)
        .order_by(CitationMention.section_label, CitationMention.created_at)
    ).all()
    contexts: list[CitationContextRead] = []
    for mention, reference in rows:
        boxes = mention.pdf_coordinates or []
        primary = boxes[0] if boxes else {}
        contexts.append(
            CitationContextRead(
                id=mention.id,
                reference_id=reference.id,
                resolved_cited_work_id=mention.resolved_cited_work_id,
                reference_title=reference.title,
                reference_raw_citation=reference.raw_citation,
                reference_doi=reference.doi,
                marker_text=mention.marker_text,
                section_label=mention.section_label,
                context_before=mention.context_before,
                context_sentence=mention.context_sentence,
                context_after=mention.context_after,
                page=mention.page,
                pdf_coordinates=boxes or None,
                pdf_x=primary.get("x"),
                pdf_y=primary.get("y"),
                pdf_w=primary.get("w"),
                pdf_h=primary.get("h"),
                source_tei_id=mention.source_tei_id,
            )
        )
    return contexts


class WorkFileRead(BaseModel):
    id: uuid.UUID
    sha256: str
    size_bytes: int
    original_filename: str | None = None
    page_count: int | None = None
    text_layer_quality: str
    status: str

    model_config = {"from_attributes": True}


@router.get("/{work_id}/files", response_model=list[WorkFileRead])
def list_work_files(work_id: uuid.UUID, db: Session = DB_DEP) -> list[File]:
    """List the files attached to a work (via FileWorkLink)."""
    if db.get(Work, work_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found")
    return list(
        db.scalars(
            select(File)
            .join(FileWorkLink, FileWorkLink.file_id == File.id)
            .where(FileWorkLink.work_id == work_id)
            .order_by(File.created_at.desc())
        ).all()
    )


@router.post("/{work_id}/files", response_model=WorkFileRead, status_code=status.HTTP_201_CREATED)
async def upload_work_file(
    work_id: uuid.UUID,
    file: UploadFile,
    db: Session = DB_DEP,
    actor: User = EDITOR_DEP,
) -> File:
    """Upload a PDF and attach it to an existing work (so a manual work isn't a dead end)."""
    work = db.get(Work, work_id)
    if work is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found")
    if file.content_type and file.content_type not in (
        "application/pdf",
        "application/octet-stream",
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Only PDF files are accepted"
        )
    pdf_bytes = await file.read(_MAX_UPLOAD_BYTES + 1)
    if len(pdf_bytes) > _MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Uploaded file exceeds 200 MB limit",
        )
    if len(pdf_bytes) < 4 or pdf_bytes[:4] != b"%PDF":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file is not a valid PDF"
        )
    file_obj, _created, _linked = attach_uploaded_pdf_to_work(
        db, work=work, filename=file.filename or "upload.pdf", pdf_bytes=pdf_bytes, actor=actor
    )
    db.commit()
    db.refresh(file_obj)
    enqueue_extraction(file_obj.id)
    return file_obj


@router.get("/annotations/search", response_model=list[AnnotationRead])
def search_annotations(
    q: str | None = Query(default=None),
    annotation_type: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = DB_DEP,
) -> list[Annotation]:
    """Search annotations across all works by selected text / note body (SPEC §8.8.7)."""
    stmt = select(Annotation)
    if q and q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(Annotation.selected_text.ilike(like), Annotation.content_markdown.ilike(like))
        )
    if annotation_type:
        stmt = stmt.where(Annotation.annotation_type == annotation_type)
    stmt = stmt.order_by(Annotation.created_at.desc()).limit(limit)
    return list(db.scalars(stmt).all())


@router.get("/{work_id}/annotations/export")
def export_work_annotations(
    work_id: uuid.UUID,
    output_format: str = Query(default="markdown", pattern="^(markdown|text)$", alias="format"),
    db: Session = DB_DEP,
) -> dict[str, str]:
    """Export a work's annotations as Markdown or plain text (SPEC §8.17.4)."""
    work = db.get(Work, work_id)
    if work is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found")
    rows = list(
        db.scalars(
            select(Annotation)
            .where(Annotation.work_id == work_id)
            .order_by(Annotation.page, Annotation.created_at)
        ).all()
    )
    title = work.canonical_title or "Untitled paper"
    lines = (
        [f"# Annotations — {title}", ""]
        if output_format == "markdown"
        else [f"Annotations — {title}", ""]
    )
    for a in rows:
        loc = f"p.{a.page}" if a.page is not None else "—"
        if output_format == "markdown":
            lines.append(f"## {a.annotation_type} ({loc})")
            if a.selected_text:
                lines.append(f"> {a.selected_text}")
            if a.content_markdown:
                lines.append(a.content_markdown)
            lines.append("")
        else:
            lines.append(f"[{a.annotation_type} {loc}]")
            if a.selected_text:
                lines.append(f'  "{a.selected_text}"')
            if a.content_markdown:
                lines.append(f"  {a.content_markdown}")
    content = "\n".join(lines) + "\n"
    extension = "md" if output_format == "markdown" else "txt"
    return {
        "filename": f"annotations-{work_id}.{extension}",
        "content_type": "text/markdown" if output_format == "markdown" else "text/plain",
        "content": content,
    }


@router.get("/{work_id}/annotations", response_model=list[AnnotationRead])
def list_work_annotations(
    work_id: uuid.UUID,
    db: Session = DB_DEP,
) -> list[Annotation]:
    """List annotations stored separately from a work's PDFs."""
    if db.get(Work, work_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found")
    return list(
        db.scalars(
            select(Annotation)
            .where(Annotation.work_id == work_id)
            .order_by(Annotation.page, Annotation.created_at)
        ).all()
    )


@router.post(
    "/{work_id}/annotations",
    response_model=AnnotationRead,
    status_code=status.HTTP_201_CREATED,
)
def create_work_annotation(
    work_id: uuid.UUID,
    payload: AnnotationCreate,
    db: Session = DB_DEP,
    actor: User = EDITOR_DEP,
) -> Annotation:
    """Create a reader annotation without modifying the source PDF."""
    if db.get(Work, work_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found")
    annotation = Annotation(
        work_id=work_id,
        file_id=payload.file_id,
        version_id=payload.version_id,
        page=payload.page,
        coordinates=payload.coordinates,
        selected_text=payload.selected_text,
        annotation_type=payload.annotation_type,
        content_markdown=payload.content_markdown,
        created_by_user_id=actor.id,
    )
    db.add(annotation)
    db.commit()
    db.refresh(annotation)
    return annotation


@router.get("/{work_id}/summaries", response_model=list[SummaryRead])
def list_summaries(work_id: uuid.UUID, db: Session = DB_DEP) -> list:
    """List stored summaries for a work (newest first)."""
    if db.get(Work, work_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found")
    return list_work_summaries(db, work_id)


@router.post(
    "/{work_id}/summaries",
    response_model=SummaryRead,
    status_code=status.HTTP_201_CREATED,
)
def create_summary(
    work_id: uuid.UUID,
    payload: SummaryCreate,
    db: Session = DB_DEP,
    _: User = EDITOR_DEP,
) -> object:
    """Generate a local (no-LLM) summary for a work and store it with provenance."""
    work = db.get(Work, work_id)
    if work is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found")
    try:
        summary = summarize_work(
            db,
            work,
            summary_type=payload.summary_type,
            max_sentences=payload.max_sentences,
            model_name=payload.model_name,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    db.commit()
    db.refresh(summary)
    return summary


def _apply_assertion_to_work(work: Work, field_name: str, value: str, source: str) -> None:
    if field_name == "title":
        work.canonical_title = value
        work.normalized_title = normalize_title(value)
        work.canonical_metadata_source = source
    elif field_name == "abstract":
        work.abstract = value
    elif field_name == "year":
        work.year = int(value) if value.isdigit() else work.year
    elif field_name == "venue":
        work.venue = value
    elif field_name == "doi":
        work.doi = normalize_doi(value)


@router.post("/{work_id}/metadata/confirm", response_model=WorkRead)
def confirm_metadata_field(
    work_id: uuid.UUID,
    payload: ConfirmFieldRequest,
    db: Session = DB_DEP,
    _: User = EDITOR_DEP,
) -> Work:
    """Lock or unlock a single field so enrichment won't overwrite it (SPEC §8.12)."""
    work = db.get(Work, work_id)
    if work is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found")
    locked = set(work.confirmed_fields or [])
    if payload.confirmed:
        locked.add(payload.field_name)
    else:
        locked.discard(payload.field_name)
    work.confirmed_fields = sorted(locked)
    work.updated_at = datetime.now(UTC)
    db.commit()
    db.refresh(work)
    return work


@router.get("/{work_id}/metadata", response_model=list[FieldReview])
def get_work_metadata(work_id: uuid.UUID, db: Session = DB_DEP) -> list[FieldReview]:
    """Return metadata assertions for a work, grouped by field, flagging conflicts."""
    work = db.get(Work, work_id)
    if work is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found")
    confirmed = set(work.confirmed_fields or [])
    rows = db.scalars(
        select(MetadataAssertion)
        .where(MetadataAssertion.entity_type == "work", MetadataAssertion.entity_id == work_id)
        .order_by(MetadataAssertion.field_name, MetadataAssertion.retrieved_at)
    ).all()
    by_field: dict[str, list[MetadataAssertion]] = {}
    for assertion in rows:
        by_field.setdefault(assertion.field_name, []).append(assertion)
    reviews: list[FieldReview] = []
    for field_name, assertions in sorted(by_field.items()):
        canonical = next((a.value for a in assertions if a.selected_as_canonical), None)
        reviews.append(
            FieldReview(
                field_name=field_name,
                canonical_value=canonical,
                has_conflict=len({a.value for a in assertions}) > 1,
                confirmed=field_name in confirmed,
                assertions=assertions,
            )
        )
    return reviews


@router.post("/{work_id}/enrich", status_code=status.HTTP_202_ACCEPTED)
def enrich_work_endpoint(
    work_id: uuid.UUID,
    db: Session = DB_DEP,
    _: User = EDITOR_DEP,
) -> dict[str, str | None]:
    """Queue external metadata enrichment for a work (needs a DOI or arXiv id)."""
    work = db.get(Work, work_id)
    if work is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found")
    if not work.doi and not work.arxiv_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Paper has no DOI or arXiv id to enrich from",
        )
    job_id = enqueue_enrichment(work_id)
    if job_id is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Enrichment queue unavailable",
        )
    return {"job_id": job_id, "status": "queued"}


@router.post("/{work_id}/metadata/select", response_model=WorkRead)
def select_metadata_assertion(
    work_id: uuid.UUID,
    payload: SelectAssertion,
    db: Session = DB_DEP,
    _: User = EDITOR_DEP,
) -> Work:
    """Choose an assertion as the canonical value for its field (a review action)."""
    work = db.get(Work, work_id)
    if work is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found")
    assertion = db.get(MetadataAssertion, payload.assertion_id)
    if assertion is None or assertion.entity_type != "work" or assertion.entity_id != work_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assertion not found")

    db.execute(
        update(MetadataAssertion)
        .where(
            MetadataAssertion.entity_type == "work",
            MetadataAssertion.entity_id == work_id,
            MetadataAssertion.field_name == assertion.field_name,
        )
        .values(selected_as_canonical=False)
    )
    assertion.selected_as_canonical = True
    if assertion.field_name in _PROMOTABLE_FIELDS:
        _apply_assertion_to_work(work, assertion.field_name, assertion.value, assertion.source)
    # Picking a canonical value locks that one field (SPEC §8.12), not the whole work.
    work.confirmed_fields = sorted(set(work.confirmed_fields or []) | {assertion.field_name})
    work.updated_at = datetime.now(UTC)
    db.commit()
    db.refresh(work)
    return work
