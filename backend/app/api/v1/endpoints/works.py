"""Work endpoints."""

import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, field_validator
from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.orm import Session

from app.api.deps import require_authenticated_user, require_contributor
from app.core.config import get_settings
from app.db.session import get_db
from app.models.ai import Embedding, Summary
from app.models.annotation import Annotation
from app.models.citation import CitationMention, Reference
from app.models.duplicate import DuplicateCandidate
from app.models.file import File, FileWorkLink
from app.models.metadata import MetadataAssertion
from app.models.organization import Rack, RackShelf, Shelf, ShelfWork, Tag, TagLink
from app.models.user import User
from app.models.work import Work, WorkVersion
from app.services import access
from app.services.audit import record_event
from app.services.file_paths import FileLocationError, resolve_backend_readable_pdf_path
from app.services.search_query import parse_search_query
from app.services.semantic_search import related_works
from app.services.storage import attach_uploaded_pdf_to_work
from app.services.summarization import list_work_summaries, summarize_work
from app.services.web_find import (
    WebCandidate,
    download_and_attach,
    find_candidates,
    iter_find_candidates,
)
from app.utils.normalization import normalize_doi, normalize_title
from app.workers.queue import (
    enqueue_embedding,
    enqueue_enrichment,
    enqueue_extraction,
    enqueue_keywords,
    enqueue_topics,
)

_MAX_UPLOAD_BYTES = 200 * 1024 * 1024  # 200 MB hard limit, mirrors /imports/upload

router = APIRouter()
DB_DEP = Depends(get_db)
# Paper mutations require at least the contributor floor; per-object scoping (own-only for
# contributors, see/modify for everyone) is enforced in the body via ``access.can_modify_work``.
CONTRIBUTOR_DEP = Depends(require_contributor)
AUTH_DEP = Depends(require_authenticated_user)


def _guard_modify_work(db: Session, actor: User, work: Work) -> None:
    """Raise 403 if ``actor`` may not modify ``work`` (contributor own-only; see+grant matrix)."""
    if not access.can_modify_work(db, actor, work):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to modify this paper",
        )


def _guard_see_work(db: Session, actor: User, work: Work) -> None:
    """Raise 404 if ``actor`` may not see ``work`` (hide existence rather than 403)."""
    if not access.can_see_work(db, actor, work):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found")


# Work columns a metadata assertion can be promoted into (mirrors the enrichment service).
_PROMOTABLE_FIELDS = {"title", "abstract", "year", "venue", "doi"}

# In-process debounce for `paper.viewed` audit events (E2): suppress a repeat view of the same
# work by the same user within this window so browsing doesn't write a row per click.
_VIEW_DEBOUNCE_S = 300.0
_recent_views: dict[tuple[str, str], float] = {}


def _should_record_view(user_id: uuid.UUID, work_id: uuid.UUID) -> bool:
    import time

    key = (str(user_id), str(work_id))
    now = time.monotonic()
    last = _recent_views.get(key)
    if last is not None and now - last < _VIEW_DEBOUNCE_S:
        return False
    _recent_views[key] = now
    if len(_recent_views) > 4096:  # bounded: drop entries older than the window
        cutoff = now - _VIEW_DEBOUNCE_S
        for k, ts in list(_recent_views.items()):
            if ts < cutoff:
                del _recent_views[k]
    return True


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
    # Per-paper representative topic terms (Phase K); rendered separately from keywords.
    topics: list[str] = []
    # The owning user (Phase H). NULL = system/agent/import "loose" paper. Drives the frontend's
    # contributor own-only edit affordance.
    created_by_user_id: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}

    @field_validator("confirmed_fields", "keywords", "topics", mode="before")
    @classmethod
    def _none_to_list(cls, value: object) -> object:
        # Pre-migration rows have NULL for these JSONB columns; treat NULL as an empty list so the
        # response stays a list rather than failing validation (would 500 the whole works list).
        return value or []


class RelatedWorkRead(BaseModel):
    work: WorkRead
    score: float
    shared_keywords: list[str] = []
    reason: str


class WorkShelfRackRef(BaseModel):
    """A rack that contains one of a paper's shelves (SEE-filtered)."""

    id: uuid.UUID
    name: str

    model_config = {"from_attributes": True}


class WorkShelfMembership(BaseModel):
    """A shelf that contains a paper, with the caller's modify-flag and its containing racks.

    ``can_modify`` reflects ``access.can_modify_shelf`` for the caller (the librarian-floor
    STRUCTURE rule), so the UI can gate the per-shelf Remove button. ``racks`` is filtered to the
    racks the caller may SEE (a shelf may sit in 0..N racks).
    """

    id: uuid.UUID
    name: str
    access_level: str
    can_modify: bool
    racks: list[WorkShelfRackRef] = []


def _looks_like_hash(text: str) -> bool:
    """True if ``text`` could be a sha256 or a sha256 prefix (all hex, 8..64 chars).

    The lower bound (8) avoids treating short common words like "deed" or "cafe" as hashes.
    """
    candidate = text.strip()
    return 8 <= len(candidate) <= 64 and all(c in "0123456789abcdefABCDEF" for c in candidate)


# Work columns that the `missing` filter can test for absence (NULL or empty string).
_MISSING_FIELDS = {
    "title": Work.canonical_title,
    "abstract": Work.abstract,
    "year": Work.year,
    "venue": Work.venue,
    "doi": Work.doi,
    "arxiv_id": Work.arxiv_id,
}

# SAFE sort allowlist: client sort key → Work column. The raw `sort` string is NEVER interpolated
# into the query; an unknown/None key falls back to the default below. This blocks column injection.
_SORT_COLUMNS = {
    "title": Work.canonical_title,
    "year": Work.year,
    "venue": Work.venue,
    "added_at": Work.created_at,
    "updated_at": Work.updated_at,
    "reading_status": Work.reading_status,
}
_DEFAULT_SORT_COLUMN = Work.updated_at


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
    sort: str | None = Query(default=None),
    order: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = DB_DEP,
    actor: User = AUTH_DEP,
) -> list[Work]:
    """List/search works by basic metadata and extraction/metadata completeness.

    ``q`` supports structured operators (``author:`` ``year:>=2020`` ``venue:`` ``tag:`` ``type:``
    ``has:pdf`` ``has:references`` ``title:``); the leftover free text matches title/abstract/DOI/
    arXiv/venue. Explicit query params (``has_pdf`` etc.) still work and take precedence.

    Access control: only papers the caller may SEE are returned (most-permissive governing shelf;
    loose papers are open; admin/owner see all).
    """
    parsed = parse_search_query(q)
    stmt = access.visible_works_query(db, actor)
    if parsed.text:
        like = f"%{parsed.text}%"
        conditions = [
            Work.canonical_title.ilike(like),
            Work.abstract.ilike(like),
            Work.doi.ilike(like),
            Work.arxiv_id.ilike(like),
            Work.venue.ilike(like),
        ]
        # A query that looks like a sha256 (full or a hex prefix) also matches the paper that owns
        # a file with that content hash — so pasting the hash shown in the file row finds the paper.
        if _looks_like_hash(parsed.text):
            owns_hash = (
                select(FileWorkLink.work_id)
                .join(File, File.id == FileWorkLink.file_id)
                .where(
                    FileWorkLink.work_id == Work.id,
                    File.sha256.ilike(f"{parsed.text.lower()}%"),
                )
                .exists()
            )
            conditions.append(owns_hash)
        stmt = stmt.where(or_(*conditions))
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
    # SAFE sort: look the key up in the allowlist (never interpolate the raw string); fall back to
    # the default column for None/unknown keys. Work.id is a stable tiebreaker for a deterministic
    # order when the sort column has ties.
    sort_column = _SORT_COLUMNS.get(sort or "", _DEFAULT_SORT_COLUMN)
    direction = sort_column.asc() if order == "asc" else sort_column.desc()
    stmt = stmt.distinct().order_by(direction, Work.id).limit(limit)
    return list(db.scalars(stmt).all())


class ReorderQueueRequest(BaseModel):
    work_ids: list[uuid.UUID]


@router.get("/reading-queue", response_model=list[WorkRead])
def reading_queue(db: Session = DB_DEP, actor: User = AUTH_DEP) -> list[Work]:
    """Return the manual reading queue (status='reading'), ordered by queue_position then recency.

    Filtered to papers the caller may SEE.
    """
    stmt = (
        access.visible_works_query(db, actor)
        .where(Work.reading_status == "reading")
        .order_by(Work.queue_position.is_(None), Work.queue_position, Work.updated_at.desc())
    )
    return list(db.scalars(stmt).all())


@router.post("/reading-queue/reorder", response_model=list[WorkRead])
def reorder_reading_queue(
    payload: ReorderQueueRequest, db: Session = DB_DEP, actor: User = CONTRIBUTOR_DEP
) -> list[Work]:
    """Set the reading-queue order to the given work id sequence (SPEC §8.17.1).

    Each listed paper must be modifiable by the caller (own-only for contributors).
    """
    for position, work_id in enumerate(payload.work_ids):
        work = db.get(Work, work_id)
        if work is None:
            continue
        _guard_modify_work(db, actor, work)
        db.execute(update(Work).where(Work.id == work_id).values(queue_position=position))
    db.commit()
    return reading_queue(db=db, actor=actor)


@router.post(
    "/from-reference/{reference_id}", response_model=WorkRead, status_code=status.HTTP_201_CREATED
)
def import_reference_as_work(
    reference_id: uuid.UUID, db: Session = DB_DEP, actor: User = CONTRIBUTOR_DEP
) -> Work:
    """Create a library work from an unresolved citation reference (SPEC §8.9 import-missing-ref).

    Idempotent: if the reference already resolves to a work, that work is returned. The new work is
    owned by the actor (``created_by_user_id``) so a contributor may later edit their own import.
    """
    reference = db.get(Reference, reference_id)
    if reference is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Reference not found")
    if reference.resolved_work_id is not None:
        existing = db.get(Work, reference.resolved_work_id)
        if existing is not None:
            return existing
    title = reference.title or reference.raw_citation or "Imported reference"
    work = Work(
        canonical_title=title,
        normalized_title=normalize_title(title),
        doi=normalize_doi(reference.doi) if reference.doi else None,
        arxiv_id=reference.arxiv_id,
        year=reference.year,
        canonical_metadata_source="reference",
        created_by_user_id=actor.id,
    )
    db.add(work)
    db.flush()
    reference.resolved_work_id = work.id
    reference.resolution_status = "local_match"
    db.commit()
    db.refresh(work)
    enqueue_embedding(work.id)
    return work


@router.post("", response_model=WorkRead, status_code=status.HTTP_201_CREATED)
def create_work(
    payload: WorkCreate,
    db: Session = DB_DEP,
    actor: User = CONTRIBUTOR_DEP,
) -> Work:
    """Create a work manually (owned by the actor so contributors may edit their own)."""
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
        created_by_user_id=actor.id,
    )
    db.add(work)
    db.commit()
    db.refresh(work)
    enqueue_embedding(work.id)  # index off the search read path (best-effort)
    return work


@router.get("/{work_id}/related", response_model=list[RelatedWorkRead])
def related_papers(
    work_id: uuid.UUID,
    limit: int = Query(default=10, ge=1, le=50),
    db: Session = DB_DEP,
    actor: User = AUTH_DEP,
) -> list[RelatedWorkRead]:
    """Return papers most similar to this one, with a "why related" reason (SPEC §8.17.2).

    The reason is the keywords the two papers share (there are no author entities to compare),
    or the embedding-similarity score when they share none. Both the source paper and every
    related paper are filtered to those the caller may SEE.
    """
    work = db.get(Work, work_id)
    if work is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found")
    _guard_see_work(db, actor, work)
    visible = access.visible_work_ids(db, actor)
    source_keywords = work.keywords or []
    related: list[RelatedWorkRead] = []
    for hit in related_works(db, work, limit=limit):
        if visible is not None and hit.work.id not in visible:
            continue
        hit_keywords = hit.work.keywords or []
        # Preserve the source paper's keyword order for a stable, readable shared list.
        hit_set = {k.lower() for k in hit_keywords}
        shared = [k for k in source_keywords if k.lower() in hit_set]
        if shared:
            reason = "Shares keywords: " + ", ".join(shared[:3])
        else:
            reason = f"Embedding similarity {hit.score:.0%}"
        related.append(
            RelatedWorkRead(
                work=WorkRead.model_validate(hit.work),
                score=hit.score,
                shared_keywords=shared,
                reason=reason,
            )
        )
    return related


@router.get("/{work_id}/shelves", response_model=list[WorkShelfMembership])
def list_work_shelves(
    work_id: uuid.UUID, db: Session = DB_DEP, actor: User = AUTH_DEP
) -> list[WorkShelfMembership]:
    """Return every shelf containing this paper that the caller may SEE ("Where is this?").

    Guarded like ``get_work`` (404 if the paper is missing or the caller can't see it). Each shelf
    is annotated with its ``access_level``, a ``can_modify`` flag (the librarian-floor STRUCTURE
    rule via ``access.can_modify_shelf`` — NOT the paper-edit rule), and its containing racks,
    themselves SEE-filtered (a private rack without a grant is omitted even when the shelf shows).
    """
    work = db.get(Work, work_id)
    if work is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found")
    _guard_see_work(db, actor, work)
    # Shelves containing this work, intersected with the shelves the caller may SEE.
    shelves = list(
        db.scalars(
            access.visible_shelves_query(db, actor)
            .join(ShelfWork, ShelfWork.shelf_id == Shelf.id)
            .where(ShelfWork.work_id == work_id)
            .order_by(Shelf.name)
        ).all()
    )
    memberships: list[WorkShelfMembership] = []
    for shelf in shelves:
        # Containing racks, filtered to the racks the caller may SEE (admin/owner unfiltered).
        racks = list(
            db.scalars(
                access.visible_racks_query(db, actor)
                .join(RackShelf, RackShelf.rack_id == Rack.id)
                .where(RackShelf.shelf_id == shelf.id)
                .order_by(Rack.name)
            ).all()
        )
        memberships.append(
            WorkShelfMembership(
                id=shelf.id,
                name=shelf.name,
                access_level=shelf.access_level,
                can_modify=access.can_modify_shelf(db, actor, shelf),
                racks=[WorkShelfRackRef(id=r.id, name=r.name) for r in racks],
            )
        )
    return memberships


@router.get("/{work_id}", response_model=WorkRead)
def get_work(work_id: uuid.UUID, db: Session = DB_DEP, actor: User = AUTH_DEP) -> Work:
    """Return one work, recording a debounced `paper.viewed` audit event (§7.6).

    The event (an INSERT + COMMIT) is skipped when the same user viewed the same work within
    ``_VIEW_DEBOUNCE_S`` (E2), so normal browsing doesn't write a row per click.
    """
    work = db.get(Work, work_id)
    if work is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found")
    _guard_see_work(db, actor, work)
    if _should_record_view(actor.id, work_id):
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
    actor: User = CONTRIBUTOR_DEP,
) -> Work:
    """Edit a work manually (contributors may edit only their own papers)."""
    work = db.get(Work, work_id)
    if work is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found")
    _guard_modify_work(db, actor, work)
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
    actor: User = CONTRIBUTOR_DEP,
) -> None:
    """Delete a paper and its dependent rows (contributors may delete only their own papers).

    Removes links and derived data (memberships, tags, assertions, summaries, embeddings,
    references, mentions, annotations, versions, duplicate candidates). The underlying File
    rows and managed PDFs are content-addressed and may be shared, so they are kept; only the
    file↔work links are removed.
    """
    work = db.get(Work, work_id)
    if work is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found")
    _guard_modify_work(db, actor, work)

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
    # Derived in-text shorthand/label (e.g. "[69]" or "(Chen et al., 2022)") taken from a linked
    # CitationMention.marker_text — lets a reference be cross-referenced with its in-text citations.
    shorthand: str | None = None

    model_config = {"from_attributes": True}


def _reference_shorthands(db: Session, reference_ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
    """Map each reference id → a representative in-text marker (the most common non-null one).

    Done as ONE batched ``reference_id IN (...)`` query (no N+1): we count each marker per
    reference and keep the most frequent, breaking ties deterministically by marker text.
    """
    if not reference_ids:
        return {}
    rows = db.execute(
        select(
            CitationMention.reference_id,
            CitationMention.marker_text,
            func.count().label("n"),
        )
        .where(
            CitationMention.reference_id.in_(reference_ids),
            CitationMention.marker_text.is_not(None),
            CitationMention.marker_text != "",
        )
        .group_by(CitationMention.reference_id, CitationMention.marker_text)
    ).all()
    best: dict[uuid.UUID, tuple[int, str]] = {}
    for reference_id, marker, count in rows:
        current = best.get(reference_id)
        # Prefer the highest count; on a tie pick the lexicographically smaller marker (stable).
        candidate = (count, marker)
        if current is None or count > current[0] or (count == current[0] and marker < current[1]):
            best[reference_id] = candidate
    return {ref_id: marker for ref_id, (_count, marker) in best.items()}


@router.get("/{work_id}/references", response_model=list[ReferenceRead])
def list_work_references(
    work_id: uuid.UUID, db: Session = DB_DEP, actor: User = AUTH_DEP
) -> list[ReferenceRead]:
    """Return the parsed bibliography (extracted references) for a work."""
    work = db.get(Work, work_id)
    if work is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found")
    _guard_see_work(db, actor, work)
    references = list(
        db.scalars(
            select(Reference)
            .where(Reference.citing_work_id == work_id)
            .order_by(Reference.created_at)
        ).all()
    )
    shorthands = _reference_shorthands(db, [ref.id for ref in references])
    return [
        ReferenceRead.model_validate(ref).model_copy(update={"shorthand": shorthands.get(ref.id)})
        for ref in references
    ]


@router.get("/{work_id}/citation-contexts", response_model=list[CitationContextRead])
def get_work_citation_contexts(
    work_id: uuid.UUID,
    db: Session = DB_DEP,
    actor: User = AUTH_DEP,
) -> list[CitationContextRead]:
    """Return in-text citation contexts for one work."""
    work = db.get(Work, work_id)
    if work is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found")
    _guard_see_work(db, actor, work)
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
    # Whether the backend can actually stream the PDF bytes. False when the file's status is
    # `extracted_discarded` (PDF removed after extract-only) or no on-disk location resolves;
    # the reader uses this to disable "Read" instead of letting the stream 404 silently.
    content_available: bool = True

    model_config = {"from_attributes": True}


# File statuses for which the original PDF bytes are deliberately not kept on the server.
_PDF_DISCARDED_STATUSES = {"extracted_discarded"}


def _file_content_available(db: Session, file: File) -> bool:
    """Return True if the PDF bytes for ``file`` can be streamed from disk right now."""
    if file.status in _PDF_DISCARDED_STATUSES:
        return False
    try:
        path = resolve_backend_readable_pdf_path(db, file=file, settings=get_settings())
    except FileLocationError:
        return False
    return path.exists() and path.is_file()


def _file_read(db: Session, file: File) -> WorkFileRead:
    return WorkFileRead.model_validate(file).model_copy(
        update={"content_available": _file_content_available(db, file)}
    )


@router.get("/{work_id}/files", response_model=list[WorkFileRead])
def list_work_files(
    work_id: uuid.UUID, db: Session = DB_DEP, actor: User = AUTH_DEP
) -> list[WorkFileRead]:
    """List the files attached to a work (via FileWorkLink)."""
    work = db.get(Work, work_id)
    if work is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found")
    _guard_see_work(db, actor, work)
    files = list(
        db.scalars(
            select(File)
            .join(FileWorkLink, FileWorkLink.file_id == File.id)
            .where(FileWorkLink.work_id == work_id)
            .order_by(File.created_at.desc())
        ).all()
    )
    return [_file_read(db, file) for file in files]


@router.post("/{work_id}/files", response_model=WorkFileRead, status_code=status.HTTP_201_CREATED)
async def upload_work_file(
    work_id: uuid.UUID,
    file: UploadFile,
    db: Session = DB_DEP,
    actor: User = CONTRIBUTOR_DEP,
) -> WorkFileRead:
    """Upload a PDF and attach it to an existing work (so a manual work isn't a dead end)."""
    work = db.get(Work, work_id)
    if work is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found")
    _guard_modify_work(db, actor, work)
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
    return _file_read(db, file_obj)


@router.get("/annotations/search", response_model=list[AnnotationRead])
def search_annotations(
    q: str | None = Query(default=None),
    annotation_type: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = DB_DEP,
    actor: User = AUTH_DEP,
) -> list[Annotation]:
    """Search annotations across all works by selected text / note body (SPEC §8.8.7).

    Restricted to annotations on papers the caller may SEE.
    """
    stmt = select(Annotation)
    if q and q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(Annotation.selected_text.ilike(like), Annotation.content_markdown.ilike(like))
        )
    if annotation_type:
        stmt = stmt.where(Annotation.annotation_type == annotation_type)
    visible = access.visible_work_ids(db, actor)
    if visible is not None:
        stmt = stmt.where(Annotation.work_id.in_(visible))
    stmt = stmt.order_by(Annotation.created_at.desc()).limit(limit)
    return list(db.scalars(stmt).all())


@router.get("/{work_id}/annotations/export")
def export_work_annotations(
    work_id: uuid.UUID,
    output_format: str = Query(default="markdown", pattern="^(markdown|text)$", alias="format"),
    db: Session = DB_DEP,
    actor: User = AUTH_DEP,
) -> dict[str, str]:
    """Export a work's annotations as Markdown or plain text (SPEC §8.17.4)."""
    work = db.get(Work, work_id)
    if work is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found")
    _guard_see_work(db, actor, work)
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
    actor: User = AUTH_DEP,
) -> list[Annotation]:
    """List annotations stored separately from a work's PDFs."""
    work = db.get(Work, work_id)
    if work is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found")
    _guard_see_work(db, actor, work)
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
    actor: User = CONTRIBUTOR_DEP,
) -> Annotation:
    """Create a reader annotation without modifying the source PDF."""
    work = db.get(Work, work_id)
    if work is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found")
    _guard_modify_work(db, actor, work)
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


@router.delete(
    "/{work_id}/annotations/{annotation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_work_annotation(
    work_id: uuid.UUID,
    annotation_id: uuid.UUID,
    db: Session = DB_DEP,
    actor: User = CONTRIBUTOR_DEP,
) -> Response:
    """Delete a reader annotation (404 when it belongs to a different paper)."""
    work = db.get(Work, work_id)
    if work is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found")
    _guard_modify_work(db, actor, work)
    annotation = db.get(Annotation, annotation_id)
    if annotation is None or annotation.work_id != work_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Annotation not found")
    db.delete(annotation)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{work_id}/summaries", response_model=list[SummaryRead])
def list_summaries(work_id: uuid.UUID, db: Session = DB_DEP, actor: User = AUTH_DEP) -> list:
    """List stored summaries for a work (newest first)."""
    work = db.get(Work, work_id)
    if work is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found")
    _guard_see_work(db, actor, work)
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
    actor: User = CONTRIBUTOR_DEP,
) -> object:
    """Generate a local (no-LLM) summary for a work and store it with provenance."""
    work = db.get(Work, work_id)
    if work is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found")
    _guard_modify_work(db, actor, work)
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
    actor: User = CONTRIBUTOR_DEP,
) -> Work:
    """Lock or unlock a single field so enrichment won't overwrite it (SPEC §8.12)."""
    work = db.get(Work, work_id)
    if work is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found")
    _guard_modify_work(db, actor, work)
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
def get_work_metadata(
    work_id: uuid.UUID, db: Session = DB_DEP, actor: User = AUTH_DEP
) -> list[FieldReview]:
    """Return metadata assertions for a work, grouped by field, flagging conflicts."""
    work = db.get(Work, work_id)
    if work is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found")
    _guard_see_work(db, actor, work)
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
    actor: User = CONTRIBUTOR_DEP,
) -> dict[str, str | None]:
    """Queue external metadata enrichment for a work (needs a DOI or arXiv id)."""
    work = db.get(Work, work_id)
    if work is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found")
    _guard_modify_work(db, actor, work)
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


@router.post("/{work_id}/extract", status_code=status.HTTP_202_ACCEPTED)
def extract_work_endpoint(
    work_id: uuid.UUID,
    db: Session = DB_DEP,
    actor: User = CONTRIBUTOR_DEP,
) -> dict[str, object]:
    """Queue GROBID extraction for every file attached to a work.

    404 if the paper is missing; ``{status: "no_files"}`` when nothing is attached; 503 if the
    extraction queue is unavailable. Per-file extraction is still available via /files/{id}/extract.
    """
    work = db.get(Work, work_id)
    if work is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found")
    _guard_modify_work(db, actor, work)
    file_ids = list(
        db.scalars(
            select(File.id)
            .join(FileWorkLink, FileWorkLink.file_id == File.id)
            .where(FileWorkLink.work_id == work_id)
            .order_by(File.created_at.desc())
        ).all()
    )
    if not file_ids:
        return {"status": "no_files", "queued": 0}
    job_ids: list[str] = []
    for file_id in file_ids:
        job_id = enqueue_extraction(file_id)
        if job_id is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Extraction queue unavailable",
            )
        job_ids.append(job_id)
    return {"status": "queued", "queued": len(job_ids), "job_ids": job_ids}


@router.post("/{work_id}/topics", status_code=status.HTTP_202_ACCEPTED)
def topic_work_endpoint(
    work_id: uuid.UUID,
    db: Session = DB_DEP,
    actor: User = CONTRIBUTOR_DEP,
) -> dict[str, str | None]:
    """Queue per-paper topic modeling for a work (representative topic terms; no precondition)."""
    work = db.get(Work, work_id)
    if work is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found")
    _guard_modify_work(db, actor, work)
    job_id = enqueue_topics(work_id)
    if job_id is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Topic queue unavailable",
        )
    return {"job_id": job_id, "status": "queued"}


@router.post("/{work_id}/keywords", status_code=status.HTTP_202_ACCEPTED)
def keywords_work_endpoint(
    work_id: uuid.UUID,
    db: Session = DB_DEP,
    actor: User = CONTRIBUTOR_DEP,
) -> dict[str, str | None]:
    """Queue per-paper keyword extraction for a work (re-runs RAKE over its text; no precondition)."""
    work = db.get(Work, work_id)
    if work is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found")
    _guard_modify_work(db, actor, work)
    job_id = enqueue_keywords(work_id)
    if job_id is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Keyword queue unavailable",
        )
    return {"job_id": job_id, "status": "queued"}


class WebCandidateRead(BaseModel):
    candidate_id: str
    source: str
    sources: list[str]
    title: str | None = None
    authors: list[str] = []
    year: int | None = None
    doi: str | None = None
    pdf_url: str | None = None
    landing_url: str | None = None
    is_oa: bool = False
    score: float = 0.0
    # find-on-web v2.1: where the "View" link actually lands after following redirects, and the
    # host to show. ``landing_url`` is now ~always populated, so the UI can always offer "View".
    resolved_url: str | None = None
    platform: str | None = None


class WebFindRequest(BaseModel):
    sources: list[str] | None = None


class WebFindResponse(BaseModel):
    candidates: list[WebCandidateRead]
    degraded_sources: list[str]
    queried_sources: list[str]


class WebFindDownloadItem(BaseModel):
    candidate_id: str
    url: str
    source: str
    # find-on-web v2: in ``unrestricted`` mode, a host not on the allow-list/known-publisher list
    # returns ``needs_confirmation``; the client re-sends the item with ``confirmed=true`` to proceed.
    confirmed: bool = False


class WebFindDownloadRequest(BaseModel):
    items: list[WebFindDownloadItem] = []


class WebFindDownloadResult(BaseModel):
    candidate_id: str
    # One of: attached | deduped | manual_upload_needed | error | blocked | needs_confirmation.
    status: str
    reason: str | None = None
    # Present (with the candidate/final URL) only when status == "needs_confirmation".
    url: str | None = None
    file: WorkFileRead | None = None


class WebFindDownloadResponse(BaseModel):
    results: list[WebFindDownloadResult]


def _candidate_read(candidate: WebCandidate) -> WebCandidateRead:
    return WebCandidateRead(
        candidate_id=candidate.candidate_id,
        source=candidate.source,
        sources=candidate.sources,
        title=candidate.title,
        authors=candidate.authors,
        year=candidate.year,
        doi=candidate.doi,
        pdf_url=candidate.pdf_url,
        landing_url=candidate.landing_url,
        is_oa=candidate.is_oa,
        score=candidate.score,
        resolved_url=candidate.resolved_url,
        platform=candidate.platform,
    )


@router.post("/{work_id}/find-on-web", response_model=WebFindResponse)
def find_on_web_endpoint(
    work_id: uuid.UUID,
    payload: WebFindRequest | None = None,
    db: Session = DB_DEP,
    actor: User = CONTRIBUTOR_DEP,
) -> WebFindResponse:
    """Search legitimate scholarly sources for candidate matches for a paper (#5).

    Read-only egress: never stores anything and never fails the whole search when a source is
    down — a degraded source is reported in ``degraded_sources``. Returns a (possibly empty)
    ranked candidate list.
    """
    work = db.get(Work, work_id)
    if work is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found")
    _guard_modify_work(db, actor, work)
    settings = get_settings()
    if not getattr(settings, "web_find_enabled", True):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Find-on-web is disabled")
    sources = payload.sources if payload else None
    result = find_candidates(db, work, settings=settings, sources=sources)
    record_event(
        db,
        "web_find.searched",
        actor_user_id=actor.id,
        entity_type="work",
        entity_id=str(work_id),
        details={
            "queried": result["queried_sources"],
            "degraded": result["degraded_sources"],
            "count": len(result["candidates"]),
        },
    )
    db.commit()
    return WebFindResponse(
        candidates=[_candidate_read(c) for c in result["candidates"]],
        degraded_sources=result["degraded_sources"],
        queried_sources=result["queried_sources"],
    )


@router.post("/{work_id}/find-on-web/download", response_model=WebFindDownloadResponse)
def find_on_web_download_endpoint(
    work_id: uuid.UUID,
    payload: WebFindDownloadRequest,
    db: Session = DB_DEP,
    actor: User = CONTRIBUTOR_DEP,
) -> WebFindDownloadResponse:
    """Download 0…N selected candidate PDFs and attach them to a paper (find-on-web v2).

    Per-item status: ``attached`` / ``deduped`` / ``manual_upload_needed`` / ``error`` /
    ``blocked`` / ``needs_confirmation``. The host classification + download-policy mode gate run
    on every redirect hop inside ``download_and_attach``: a shadow-library / private-IP / bad-scheme
    host is ``blocked`` (stores nothing) in every mode; in ``unrestricted`` mode an unknown public
    host returns ``needs_confirmation`` (with the URL) unless the item set ``confirmed=true``. A
    failed item never aborts the others; an empty ``items`` list is a no-op.
    """
    work = db.get(Work, work_id)
    if work is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found")
    _guard_modify_work(db, actor, work)
    settings = get_settings()
    if not getattr(settings, "web_find_enabled", True):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Find-on-web is disabled")
    results: list[WebFindDownloadResult] = []
    for item in payload.items:
        outcome = download_and_attach(
            db,
            work=work,
            candidate_url=item.url,
            source=item.source,
            actor=actor,
            settings=settings,
            confirmed=item.confirmed,
            file_read=_file_read,
        )
        results.append(
            WebFindDownloadResult(
                candidate_id=item.candidate_id,
                status=outcome["status"],
                reason=outcome.get("reason"),
                url=outcome.get("url"),
                file=outcome.get("file"),
            )
        )
    return WebFindDownloadResponse(results=results)


@router.post("/{work_id}/find-on-web/stream")
def find_on_web_stream_endpoint(
    work_id: uuid.UUID,
    payload: WebFindRequest | None = None,
    db: Session = DB_DEP,
    actor: User = CONTRIBUTOR_DEP,
) -> StreamingResponse:
    """Stream find-on-web search progress as NDJSON (find-on-web v2). Contributor-gated.

    Emits one JSON object per line (``application/x-ndjson``):
      * ``{"type":"source","source":<name>,"status":"querying"}`` as each source starts,
      * ``{"type":"source","source":<name>,"status":"done","count":N}`` / ``"status":"failed"``
        as each source finishes,
      * a final ``{"type":"result","candidates":[...],"degraded_sources":[...],
        "queried_sources":[...]}`` line.
    """
    work = db.get(Work, work_id)
    if work is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found")
    _guard_modify_work(db, actor, work)
    settings = get_settings()
    if not getattr(settings, "web_find_enabled", True):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Find-on-web is disabled")
    sources = payload.sources if payload else None

    def _generate() -> Iterator[str]:
        # Drive the generator directly so each per-source event is flushed AS the source runs
        # (incremental progress), not buffered to completion. The generator yields the final
        # "result" event last (after dedup + rank + redirect resolution).
        for event in iter_find_candidates(db, work, settings=settings, sources=sources):
            if event.get("type") == "result":
                record_event(
                    db,
                    "web_find.searched",
                    actor_user_id=actor.id,
                    entity_type="work",
                    entity_id=str(work_id),
                    details={
                        "queried": event["queried_sources"],
                        "degraded": event["degraded_sources"],
                        "count": len(event["candidates"]),
                        "streamed": True,
                    },
                )
                db.commit()
                final = {
                    "type": "result",
                    "candidates": [_candidate_read(c).model_dump() for c in event["candidates"]],
                    "degraded_sources": event["degraded_sources"],
                    "queried_sources": event["queried_sources"],
                }
                yield json.dumps(final) + "\n"
            else:
                yield json.dumps(event) + "\n"

    return StreamingResponse(_generate(), media_type="application/x-ndjson")


@router.post("/{work_id}/metadata/select", response_model=WorkRead)
def select_metadata_assertion(
    work_id: uuid.UUID,
    payload: SelectAssertion,
    db: Session = DB_DEP,
    actor: User = CONTRIBUTOR_DEP,
) -> Work:
    """Choose an assertion as the canonical value for its field (a review action)."""
    work = db.get(Work, work_id)
    if work is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found")
    _guard_modify_work(db, actor, work)
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
