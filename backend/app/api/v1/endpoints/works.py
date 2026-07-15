"""Work endpoints."""

import json
import math
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, UploadFile, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, field_validator
from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import require_authenticated_user, require_contributor, require_min_role
from app.core.config import get_settings
from app.core.security import Role
from app.db.session import get_db
from app.models.agent import AgentFile
from app.models.ai import Embedding, Summary
from app.models.annotation import Annotation
from app.models.citation import CitationMention, Reference, ReferenceCitation
from app.models.duplicate import DuplicateCandidate
from app.models.file import File, FileWorkLink
from app.models.metadata import MetadataAssertion
from app.models.organization import Rack, RackShelf, Shelf, ShelfWork, Tag, TagLink
from app.models.user import User
from app.models.work import Work, WorkVersion
from app.services import access
from app.services.app_config import (
    effective_accept_policy,
    effective_citing_papers_fetch_cap,
    effective_max_papers_per_page,
)
from app.services.audit import record_event
from app.services.citation_graph import build_citation_neighborhood
from app.services.citing_papers import rescan_external_papers_for_new_work
from app.services.doi_conflict import message_from_exception
from app.services.duplicate_resolution import (
    has_reversible_shadow,
    linked_work_ids,
    merge_preview,
    merge_works,
    unmerge_work,
)
from app.services.file_paths import (
    FileLocationError,
    resolve_streamable_pdf_path,
)
from app.services.queue_capacity import assert_queue_has_capacity
from app.services.reference_links import references_for_work
from app.services.reference_matching import (
    rescan_references_for_new_work,
    run_matching_for_references,
)
from app.services.semantic_search import related_works
from app.services.storage import (
    attach_uploaded_pdf_to_work,
    mark_extraction_requested,
    probe_pdf_openable,
)
from app.services.summarization import list_work_summaries, summarize_work
from app.services.web_find import (
    WebCandidate,
    download_and_attach,
    find_candidates,
    iter_find_candidates,
)

# Re-exported: the Library filter query builder moved to the service layer (S4) so services
# (saved_filters, graphs, exports) no longer import from an endpoint module. Endpoint code and
# older imports keep using this name.
from app.services.works_query import build_works_query  # noqa: F401
from app.utils.normalization import normalize_doi, normalize_title, similarity_pct
from app.workers.queue import (
    enqueue_citing_fetch,
    enqueue_embedding,
    enqueue_enrichment,
    enqueue_extraction,
    enqueue_keywords,
    enqueue_topics,
    enqueue_work_summary,
)

_MAX_UPLOAD_BYTES = 200 * 1024 * 1024  # 200 MB hard limit, mirrors /imports/upload

# Fallback Library page size when a user has no ``papers_per_page`` preference (D18).
DEFAULT_PAPERS_PER_PAGE = 100

router = APIRouter()
DB_DEP = Depends(get_db)
# Paper mutations require at least the contributor floor; per-object scoping (own-only for
# contributors, see/modify for everyone) is enforced in the body via ``access.can_modify_work``.
CONTRIBUTOR_DEP = Depends(require_contributor)
EDITOR_DEP = Depends(require_min_role(Role.EDITOR))
AUTH_DEP = Depends(require_authenticated_user)


def _commit_or_doi_409(db: Session) -> None:
    """Commit, translating a ``uq_works_doi`` collision into a clear 409 instead of a raw 500.

    A manual edit / metadata-apply that assigns a paper a DOI already held by a *different* paper
    fails at commit with an ``IntegrityError`` (issue 3). Any other integrity error is re-raised
    unchanged so genuine bugs still surface. On a DOI collision the transaction is rolled back and a
    message naming the offending DOI + the paper that holds it is returned as HTTP 409.
    """
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        message = message_from_exception(db, exc)  # None → not a DOI collision
        if message is None:
            raise
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=message) from None


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


class WorkShelfRef(BaseModel):
    """A shelf a paper sits on, SEE-filtered (id + name only; D32 library column)."""

    id: uuid.UUID
    name: str

    model_config = {"from_attributes": True}


class WorkRackRef(BaseModel):
    """A rack containing one of a paper's (see-able) shelves, SEE-filtered (D32 library column)."""

    id: uuid.UUID
    name: str

    model_config = {"from_attributes": True}


class WorkTagRef(BaseModel):
    """A tag applied to a paper (id + name + colour; batch10 library column)."""

    id: uuid.UUID
    name: str
    color: str | None = None

    model_config = {"from_attributes": True}


class WorkRead(BaseModel):
    id: uuid.UUID
    canonical_title: str | None = None
    abstract: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    venue: str | None = None
    year: int | None = None
    reading_status: str
    # Per-paper processing error (F2): "<stage>: <reason>" when a background enrich/keyword/topic job
    # failed for this paper; NULL when clear. Drives a "processing failed" badge.
    processing_error: str | None = None
    # Origin marker; "agent_index_only" on a not-yet-extracted local-agent stub (B6), cleared once
    # the paper is extracted/teleported — the library UI badges it "not extracted" while set.
    canonical_metadata_source: str | None = None
    # User-chosen primary file for one-click "Read" (#16); NULL → the UI uses the first file.
    main_file_id: uuid.UUID | None = None
    confirmed_fields: list[str] = []
    keywords: list[str] = []
    # Per-paper representative topic terms (Phase K); rendered separately from keywords.
    topics: list[str] = []
    # The owning user (Phase H). NULL = system/agent/import "loose" paper. Drives the frontend's
    # contributor own-only edit affordance.
    created_by_user_id: uuid.UUID | None = None
    # External citation-count snapshot (Track C P1); NULL for papers with no resolvable id. The
    # source is the connector it came from and ``fetched_at`` is when it was last refreshed.
    citation_count: int | None = None
    citation_count_source: str | None = None
    citation_count_fetched_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    # Duplicate-merge shadow marker (Batch D): non-null on a hidden shadow (never returned by the
    # library/search paths). ``has_reversible_shadow`` is computed (get_work only): true when this
    # paper is a merge base whose most recent merge can still be undone — drives the Unmerge button.
    merged_into_id: uuid.UUID | None = None
    has_reversible_shadow: bool = False
    # Library columns (D32): the paper's SEE-filtered shelves and their SEE-filtered racks. Populated
    # by ``list_works`` (batched across the page); other endpoints leave these empty.
    shelves: list[WorkShelfRef] = []
    racks: list[WorkRackRef] = []
    # Library columns (batch10): number of files attached, applied tags, and status "badges"
    # (extraction/text-layer/conflict flags). Populated by ``list_works`` (batched across the page);
    # other endpoints leave file_count=0 and the lists empty.
    file_count: int = 0
    tags: list[WorkTagRef] = []
    badges: list[str] = []
    # Reference/citation count columns (batch 12). ``reference_count`` = references this paper cites;
    # ``local_reference_count`` = distinct OTHER local papers it references; ``local_citation_count`` =
    # distinct OTHER local papers that reference it. (External ``citation_count`` is above.) Populated
    # by ``list_works`` (batched across the page); other endpoints leave them 0.
    reference_count: int = 0
    local_reference_count: int = 0
    local_citation_count: int = 0

    model_config = {"from_attributes": True}

    @field_validator(
        "confirmed_fields",
        "keywords",
        "topics",
        "shelves",
        "racks",
        "tags",
        "badges",
        mode="before",
    )
    @classmethod
    def _none_to_list(cls, value: object) -> object:
        # Pre-migration rows have NULL for these JSONB columns; treat NULL as an empty list so the
        # response stays a list rather than failing validation (would 500 the whole works list).
        return value or []


class PaginatedWorks(BaseModel):
    """Server-controlled Library pagination envelope (D18)."""

    items: list[WorkRead]
    total: int
    page: int
    pages: int
    per_page: int


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


# Count-based sort expressions (library count columns). Correlated scalar subqueries so a page can be
# ordered by a per-work count at the SQL level (before pagination). Counts are 0-based (never NULL);
# self-references/-citations are excluded so the counts mean "how many OTHER local papers".
_FILE_COUNT_SORT = (
    select(func.count(FileWorkLink.file_id))
    .where(FileWorkLink.work_id == Work.id)
    .correlate(Work)
    .scalar_subquery()
)
_REFERENCE_COUNT_SORT = (
    select(func.count(ReferenceCitation.id))
    .where(ReferenceCitation.citing_work_id == Work.id)
    .correlate(Work)
    .scalar_subquery()
)
_LOCAL_REFERENCE_COUNT_SORT = (
    select(func.count(func.distinct(Reference.resolved_work_id)))
    .select_from(ReferenceCitation)
    .join(Reference, Reference.id == ReferenceCitation.reference_id)
    .where(
        ReferenceCitation.citing_work_id == Work.id,
        Reference.resolved_work_id.is_not(None),
        Reference.resolved_work_id != Work.id,
    )
    .correlate(Work)
    .scalar_subquery()
)
_LOCAL_CITATION_COUNT_SORT = (
    select(func.count(func.distinct(ReferenceCitation.citing_work_id)))
    .select_from(Reference)
    .join(ReferenceCitation, ReferenceCitation.reference_id == Reference.id)
    .where(
        Reference.resolved_work_id == Work.id,
        ReferenceCitation.citing_work_id != Work.id,
    )
    .correlate(Work)
    .scalar_subquery()
)

# SAFE sort allowlist: client sort key → Work column / expression. The raw `sort` string is NEVER
# interpolated into the query; an unknown/None key falls back to the default below (blocks injection).
_SORT_COLUMNS = {
    "title": Work.canonical_title,
    "year": Work.year,
    "venue": Work.venue,
    "added_at": Work.created_at,
    "updated_at": Work.updated_at,
    "reading_status": Work.reading_status,
    "file_count": _FILE_COUNT_SORT,
    "reference_count": _REFERENCE_COUNT_SORT,
    "citation_count": Work.citation_count,
    "local_reference_count": _LOCAL_REFERENCE_COUNT_SORT,
    "local_citation_count": _LOCAL_CITATION_COUNT_SORT,
}
# These sorts can carry NULL (Work.citation_count) — order NULLs last regardless of direction so
# "unknown" never floats to the top. The count subqueries are 0-based, so this is a harmless no-op.
_NULLS_LAST_SORTS = {
    "file_count",
    "reference_count",
    "citation_count",
    "local_reference_count",
    "local_citation_count",
}
# Sorts backed by correlated scalar subqueries (not Work columns). The works query is SELECT
# DISTINCT and Postgres requires DISTINCT ORDER BY expressions to appear in the select list, so
# these are selected as a labelled extra column and ordered by the label. The count is
# deterministic per work, so DISTINCT semantics are unchanged. (SQLite tolerates the bare
# subquery ORDER BY, which is why only the Postgres deployment surfaced this.)
_SUBQUERY_SORTS = {
    "file_count",
    "reference_count",
    "local_reference_count",
    "local_citation_count",
}
_DEFAULT_SORT_COLUMN = Work.updated_at


def _batch_shelf_rack_refs(
    db: Session, actor: User, work_ids: list[uuid.UUID]
) -> tuple[dict[uuid.UUID, list[WorkShelfRef]], dict[uuid.UUID, list[WorkRackRef]]]:
    """Batch-load the SEE-filtered shelves (and their SEE-filtered racks) for a page of works.

    Two grouped queries total (O(1) per page, not O(n)): one over ``ShelfWork`` intersected with the
    caller's visible shelves, one over ``RackShelf`` intersected with the caller's visible racks.
    Returns ``(work_id -> shelves, work_id -> racks)``; a work's racks are the deduped union of the
    racks containing any of its (see-able) shelves.
    """
    work_shelves: dict[uuid.UUID, list[WorkShelfRef]] = {}
    work_racks: dict[uuid.UUID, list[WorkRackRef]] = {}
    if not work_ids:
        return work_shelves, work_racks
    # Shelves the caller may SEE that contain any of the page's works (SEE-filtered subquery).
    shelf_sub = access.visible_shelves_query(db, actor).subquery()
    shelf_rows = db.execute(
        select(ShelfWork.work_id, shelf_sub.c.id, shelf_sub.c.name)
        .join(shelf_sub, shelf_sub.c.id == ShelfWork.shelf_id)
        .where(ShelfWork.work_id.in_(work_ids))
        .order_by(shelf_sub.c.name)
    ).all()
    shelf_to_works: dict[uuid.UUID, list[uuid.UUID]] = {}
    for work_id, shelf_id, shelf_name in shelf_rows:
        work_shelves.setdefault(work_id, []).append(WorkShelfRef(id=shelf_id, name=shelf_name))
        shelf_to_works.setdefault(shelf_id, []).append(work_id)
    if not shelf_to_works:
        return work_shelves, work_racks
    # Racks the caller may SEE that contain any of those shelves (SEE-filtered subquery).
    rack_sub = access.visible_racks_query(db, actor).subquery()
    rack_rows = db.execute(
        select(RackShelf.shelf_id, rack_sub.c.id, rack_sub.c.name)
        .join(rack_sub, rack_sub.c.id == RackShelf.rack_id)
        .where(RackShelf.shelf_id.in_(list(shelf_to_works)))
        .order_by(rack_sub.c.name)
    ).all()
    seen: dict[uuid.UUID, set[uuid.UUID]] = {}
    for shelf_id, rack_id, rack_name in rack_rows:
        for work_id in shelf_to_works.get(shelf_id, []):
            rack_ids = seen.setdefault(work_id, set())
            if rack_id in rack_ids:
                continue
            rack_ids.add(rack_id)
            work_racks.setdefault(work_id, []).append(WorkRackRef(id=rack_id, name=rack_name))
    return work_shelves, work_racks


# Text-layer qualities that are worth surfacing as a badge (good/unknown are unremarkable).
_TEXT_QUALITY_BADGES = {"poor": "text_poor", "none": "text_none", "ocr_added": "ocr_added"}


def _batch_library_columns(
    db: Session, works: list[Work]
) -> tuple[dict[uuid.UUID, int], dict[uuid.UUID, list[WorkTagRef]], dict[uuid.UUID, list[str]]]:
    """Batch-load the file count, applied tags, and status badges for a page of works.

    Four grouped queries total (O(1) per page, not O(n)): files-per-work (for count + status +
    text-layer badges), applied tags, and a conflict probe over metadata assertions. Returns
    ``(work_id -> file_count, work_id -> tags, work_id -> badges)``. Badge tokens are UI-agnostic
    strings (``extracted``/``extract_failed``/``not_extracted``/``text_poor``/``text_none``/
    ``ocr_added``/``conflicts``) that the frontend maps to labels + colours.
    """
    file_counts: dict[uuid.UUID, int] = {}
    work_tags: dict[uuid.UUID, list[WorkTagRef]] = {}
    badges: dict[uuid.UUID, list[str]] = {}
    work_ids = [w.id for w in works]
    if not work_ids:
        return file_counts, work_tags, badges

    # Files linked to each work → count + the set of statuses + text-layer qualities present.
    statuses: dict[uuid.UUID, set[str]] = {}
    qualities: dict[uuid.UUID, set[str]] = {}
    file_rows = db.execute(
        select(FileWorkLink.work_id, File.status, File.text_layer_quality)
        .join(File, File.id == FileWorkLink.file_id)
        .where(FileWorkLink.work_id.in_(work_ids))
    ).all()
    for work_id, status_, quality in file_rows:
        file_counts[work_id] = file_counts.get(work_id, 0) + 1
        statuses.setdefault(work_id, set()).add(status_)
        qualities.setdefault(work_id, set()).add(quality)

    # Applied tags per work (entity_type discriminator == "work").
    tag_rows = db.execute(
        select(TagLink.entity_id, Tag.id, Tag.name, Tag.color)
        .join(Tag, Tag.id == TagLink.tag_id)
        .where(TagLink.entity_type == "work", TagLink.entity_id.in_(work_ids))
        .order_by(Tag.normalized_name)
    ).all()
    for entity_id, tag_id, tag_name, tag_color in tag_rows:
        work_tags.setdefault(entity_id, []).append(
            WorkTagRef(id=tag_id, name=tag_name, color=tag_color)
        )

    # Conflict probe: a work has a metadata conflict if any field carries ≥2 distinct values
    # (mirrors the paper-view "conflicts" indicator). One grouped query for the whole page.
    conflict_ids = {
        row[0]
        for row in db.execute(
            select(MetadataAssertion.entity_id)
            .where(
                MetadataAssertion.entity_type == "work",
                MetadataAssertion.entity_id.in_(work_ids),
            )
            .group_by(MetadataAssertion.entity_id, MetadataAssertion.field_name)
            .having(func.count(func.distinct(MetadataAssertion.value)) > 1)
        ).all()
    }

    for work in works:
        tokens: list[str] = []
        # A not-yet-extracted local-agent stub reads as "not extracted" regardless of file rows.
        if work.canonical_metadata_source == "agent_index_only":
            tokens.append("not_extracted")
        work_statuses = statuses.get(work.id, set())
        if "extracted" in work_statuses:
            tokens.append("extracted")
        if "extract_failed" in work_statuses:
            tokens.append("extract_failed")
        for quality in sorted(qualities.get(work.id, set())):
            token = _TEXT_QUALITY_BADGES.get(quality)
            if token:
                tokens.append(token)
        if work.id in conflict_ids:
            tokens.append("conflicts")
        if tokens:
            badges[work.id] = tokens
    return file_counts, work_tags, badges


def _batch_reference_counts(
    db: Session, works: list[Work]
) -> tuple[dict[uuid.UUID, int], dict[uuid.UUID, int], dict[uuid.UUID, int]]:
    """Batch-load the reference/citation counts for a page of works (3 grouped queries, no N+1).

    Returns ``(reference_count, local_reference_count, local_citation_count)`` per work id:
    references this paper cites, distinct OTHER local papers it references, and distinct OTHER local
    papers that reference it. Self-references/-citations are excluded so the local counts mean
    "how many other local papers".
    """
    ref_counts: dict[uuid.UUID, int] = {}
    local_ref_counts: dict[uuid.UUID, int] = {}
    local_cit_counts: dict[uuid.UUID, int] = {}
    work_ids = [w.id for w in works]
    if not work_ids:
        return ref_counts, local_ref_counts, local_cit_counts

    # Total references this work cites.
    for wid, n in db.execute(
        select(ReferenceCitation.citing_work_id, func.count(ReferenceCitation.id))
        .where(ReferenceCitation.citing_work_id.in_(work_ids))
        .group_by(ReferenceCitation.citing_work_id)
    ).all():
        ref_counts[wid] = int(n)

    # Distinct OTHER local works this work references (reference resolved to a local work != itself).
    for wid, n in db.execute(
        select(
            ReferenceCitation.citing_work_id,
            func.count(func.distinct(Reference.resolved_work_id)),
        )
        .join(Reference, Reference.id == ReferenceCitation.reference_id)
        .where(
            ReferenceCitation.citing_work_id.in_(work_ids),
            Reference.resolved_work_id.is_not(None),
            Reference.resolved_work_id != ReferenceCitation.citing_work_id,
        )
        .group_by(ReferenceCitation.citing_work_id)
    ).all():
        local_ref_counts[wid] = int(n)

    # Distinct OTHER local works that reference this work.
    for wid, n in db.execute(
        select(
            Reference.resolved_work_id,
            func.count(func.distinct(ReferenceCitation.citing_work_id)),
        )
        .join(ReferenceCitation, ReferenceCitation.reference_id == Reference.id)
        .where(
            Reference.resolved_work_id.in_(work_ids),
            ReferenceCitation.citing_work_id != Reference.resolved_work_id,
        )
        .group_by(Reference.resolved_work_id)
    ).all():
        local_cit_counts[wid] = int(n)

    return ref_counts, local_ref_counts, local_cit_counts


@router.get("", response_model=PaginatedWorks)
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
    page: int = Query(default=1, ge=1),
    per_page: int | None = Query(default=None, ge=1),
    db: Session = DB_DEP,
    actor: User = AUTH_DEP,
) -> PaginatedWorks:
    """List/search works by basic metadata and extraction/metadata completeness (paginated).

    ``q`` supports structured operators (``author:`` ``year:>=2020`` ``venue:`` ``tag:`` ``type:``
    ``title:`` ``doi:`` ``arxiv:`` ``status:`` ``shelf:`` ``rack:`` ``cites:`` ``cited_by_local:``
    ``keyword:`` ``topic:``
    ``abstract:`` ``summary:`` ``fulltext:`` ``file:`` ``duplicate:`` ``version:`` ``warning:``
    ``has:pdf|references|notes|annotations|summary|abstract|grobid|ocr``); the leftover free text
    matches title/abstract/DOI/arXiv/venue. Explicit query params (``has_pdf`` etc.) still work and take
    precedence. ``shelf:``/``rack:`` and ``cites:``/``cited_by_local:`` compose ON TOP of the SEE
    filter (only see-able shelves/racks contribute; they never widen visibility).

    Pagination (D18): ``per_page`` overrides the caller's ``papers_per_page`` preference (else the
    server default); the effective size is clamped to the admin global maximum. ``page`` is clamped
    into ``1..pages``. The response envelope carries ``items``/``total``/``page``/``pages``/
    ``per_page``. Each item also carries its SEE-filtered ``shelves``/``racks`` (D32).

    Access control: only papers the caller may SEE are returned (most-permissive governing shelf;
    loose papers are open; admin/owner see all). The filter body is ``build_works_query`` (reused by
    the saved-filter/graph/export resolvers); ``list_works`` adds sort + pagination.
    """
    stmt = build_works_query(
        db,
        actor,
        q=q,
        reading_status=reading_status,
        shelf_id=shelf_id,
        rack_id=rack_id,
        tag_id=tag_id,
        has_pdf=has_pdf,
        has_references=has_references,
        missing=missing,
    )
    # Effective page size: explicit override, else the user preference, else the server default —
    # then clamp to the admin-controlled global maximum.
    requested = (
        per_page if per_page is not None else (actor.papers_per_page or DEFAULT_PAPERS_PER_PAGE)
    )
    effective_per_page = max(1, min(requested, effective_max_papers_per_page(db)))
    # Total over the SAME filtered query (respects the query's DISTINCT) before limit/offset.
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    pages = max(1, math.ceil(total / effective_per_page))
    page = min(max(page, 1), pages)
    # SAFE sort: look the key up in the allowlist (never interpolate the raw string); fall back to
    # the default column for None/unknown keys. Work.id is a stable tiebreaker for a deterministic
    # order when the sort column has ties.
    sort_key = sort or ""
    sort_column = _SORT_COLUMNS.get(sort_key, _DEFAULT_SORT_COLUMN)
    if sort_key in _SUBQUERY_SORTS:
        # Select the correlated count as a labelled column so the DISTINCT query may ORDER BY it
        # on Postgres; ``db.scalars`` below still yields only the Work entities.
        sort_column = sort_column.label("sort_value")
        stmt = stmt.add_columns(sort_column)
    base_direction = sort_column.asc() if order == "asc" else sort_column.desc()
    # NULL-carrying sorts (citation_count) order NULLs last regardless of direction.
    direction = base_direction.nullslast() if sort_key in _NULLS_LAST_SORTS else base_direction
    stmt = (
        stmt.order_by(direction, Work.id)
        .offset((page - 1) * effective_per_page)
        .limit(effective_per_page)
    )
    works = list(db.scalars(stmt).all())
    work_shelves, work_racks = _batch_shelf_rack_refs(db, actor, [w.id for w in works])
    file_counts, work_tags, work_badges = _batch_library_columns(db, works)
    ref_counts, local_ref_counts, local_cit_counts = _batch_reference_counts(db, works)
    items = [
        WorkRead.model_validate(w).model_copy(
            update={
                "shelves": work_shelves.get(w.id, []),
                "racks": work_racks.get(w.id, []),
                "file_count": file_counts.get(w.id, 0),
                "tags": work_tags.get(w.id, []),
                "badges": work_badges.get(w.id, []),
                "reference_count": ref_counts.get(w.id, 0),
                "local_reference_count": local_ref_counts.get(w.id, 0),
                "local_citation_count": local_cit_counts.get(w.id, 0),
            }
        )
        for w in works
    ]
    return PaginatedWorks(
        items=items, total=total, page=page, pages=pages, per_page=effective_per_page
    )


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
    # Reverse-rescan (batch 12): this newly-created work may also be what OTHER still-external
    # references cite — link them now (fuzzy stays soft; toggle read from AppConfig) so importing one
    # citation doesn't leave its siblings pointing "external".
    rescan_references_for_new_work(db, work, accept_policy=effective_accept_policy(db))
    rescan_external_papers_for_new_work(db, work)
    db.commit()
    db.refresh(work)
    enqueue_embedding(work.id)
    return work


@router.post(
    "/from-citing/{external_paper_id}",
    response_model=WorkRead,
    status_code=status.HTTP_201_CREATED,
)
def import_citing_paper_as_work(
    external_paper_id: uuid.UUID, db: Session = DB_DEP, actor: User = CONTRIBUTOR_DEP
) -> Work:
    """Create a library work from a cached external citing paper (UX batch).

    Works WITHOUT an identifier too — the work is built from the cached title/year/venue/authors
    ("Create paper"); when a DOI/arXiv id is present, enrichment fills the rest in the background
    ("Direct import"). Idempotent: an already-resolved citing paper returns its existing work.
    """
    from app.models.external_citation import ExternalPaper
    from app.services.default_shelf import place_on_default_if_loose

    external = db.get(ExternalPaper, external_paper_id)
    if external is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Citing paper not found")
    if external.resolved_work_id is not None:
        existing = db.get(Work, external.resolved_work_id)
        if existing is not None:
            return existing
    title = external.title or "Imported citing paper"
    doi = normalize_doi(external.doi) if external.doi else None
    work = Work(
        canonical_title=title,
        normalized_title=normalize_title(title),
        doi=doi,
        arxiv_id=external.arxiv_id,
        year=external.year,
        venue=external.venue,
        canonical_metadata_source="citing",
        created_by_user_id=actor.id,
    )
    db.add(work)
    db.flush()
    if external.authors:
        db.add(
            MetadataAssertion(
                entity_type="work",
                entity_id=work.id,
                field_name="authors",
                value=external.authors,
                source="citing",
                confidence=1.0,
                selected_as_canonical=True,
            )
        )
    external.resolved_work_id = work.id
    # No free-floating papers (#1) + reverse-rescan (batch 12): the new paper may be what other
    # external references/citers point at.
    place_on_default_if_loose(db, work.id, actor_id=actor.id)
    rescan_references_for_new_work(db, work, accept_policy=effective_accept_policy(db))
    rescan_external_papers_for_new_work(db, work)
    db.commit()
    db.refresh(work)
    enqueue_embedding(work.id)
    if doi or external.arxiv_id:
        enqueue_enrichment(work.id)
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
    # No free-floating papers (#1): a manually-created paper lands on the default shelf (committed
    # separately so the work's own create/refresh flow is unaffected).
    from app.services.default_shelf import place_on_default_if_loose

    place_on_default_if_loose(db, work.id, actor_id=actor.id)
    # Reverse-rescan (batch 12): link any still-external references that cite this new paper.
    rescan_references_for_new_work(db, work, accept_policy=effective_accept_policy(db))
    rescan_external_papers_for_new_work(db, work)
    db.commit()
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


@router.get("/{work_id}/related-links", response_model=list[WorkRead])
def list_related_links(
    work_id: uuid.UUID, db: Session = DB_DEP, actor: User = AUTH_DEP
) -> list[WorkRead]:
    """Return papers bidirectionally LINKED to this one (Batch D "Link" — related / same work).

    Distinct from ``/related`` (embedding similarity): these are user-declared relationships. Both
    the paper and every linked paper are SEE-filtered; merged shadows never appear.
    """
    work = db.get(Work, work_id)
    if work is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found")
    _guard_see_work(db, actor, work)
    ids = linked_work_ids(db, work_id)
    if not ids:
        return []
    visible = access.visible_work_ids(db, actor)
    linked = db.scalars(select(Work).where(Work.id.in_(ids), Work.merged_into_id.is_(None))).all()
    return [WorkRead.model_validate(w) for w in linked if visible is None or w.id in visible]


@router.post("/{work_id}/unmerge", response_model=WorkRead)
def unmerge_paper(
    work_id: uuid.UUID,
    db: Session = DB_DEP,
    actor: User = CONTRIBUTOR_DEP,
) -> WorkRead:
    """Undo the most recent merge into this paper, restoring the shadow to a standalone paper.

    One transaction: moves the merged entities back, un-redirects the incoming references, clears
    the base fields the merge filled, and removes the conflict assertions it added. 400 if the paper
    has no reversible merge to undo.
    """
    work = db.get(Work, work_id)
    if work is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found")
    _guard_modify_work(db, actor, work)
    try:
        unmerge_work(db, base_id=work_id, actor=actor)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    db.commit()
    db.refresh(work)
    return WorkRead.model_validate(work).model_copy(
        update={"has_reversible_shadow": has_reversible_shadow(db, work_id)}
    )


class MergePaperRequest(BaseModel):
    source_work_id: uuid.UUID


class MergePaperPreview(BaseModel):
    base_work_id: uuid.UUID
    source_work_id: uuid.UUID
    fill_fields: list[str]
    conflict_fields: list[str]
    file_count: int
    incoming_reference_count: int
    will_flatten: bool


def _merge_pair(
    db: Session, actor: User, base_id: uuid.UUID, source_id: uuid.UUID
) -> tuple[Work, Work]:
    """Resolve + permission-check the (base, source) papers for an arbitrary merge (issue 4)."""
    if base_id == source_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot merge a paper into itself"
        )
    base = db.get(Work, base_id)
    if base is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found")
    _guard_modify_work(db, actor, base)
    source = db.get(Work, source_id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source paper not found")
    _guard_modify_work(db, actor, source)
    return base, source


@router.get("/{work_id}/merge-preview", response_model=MergePaperPreview)
def merge_paper_preview(
    work_id: uuid.UUID,
    source_work_id: uuid.UUID = Query(...),
    db: Session = DB_DEP,
    actor: User = CONTRIBUTOR_DEP,
) -> MergePaperPreview:
    """Preview merging ``source_work_id`` INTO this paper (issue 4) — read-only, no changes."""
    base, source = _merge_pair(db, actor, work_id, source_work_id)
    return MergePaperPreview(**merge_preview(db, base=base, source=source))


@router.post("/{work_id}/merge", response_model=WorkRead)
def merge_paper(
    work_id: uuid.UUID,
    payload: MergePaperRequest,
    db: Session = DB_DEP,
    actor: User = CONTRIBUTOR_DEP,
) -> WorkRead:
    """Merge another paper INTO this one (issue 4), reusing the duplicate-resolution merge.

    Fills this paper's empty fields from the source, records differing values as conflicts, moves
    the source's files/tags/shelves/references/annotations here, and hides the source as a reversible
    shadow (undo via ``/unmerge``). This exposes the existing merge for any two papers, not only
    duplicate-scan candidates. Requires modify rights on both.
    """
    base, source = _merge_pair(db, actor, work_id, payload.source_work_id)
    try:
        merge_works(db, base=base, source=source, actor=actor)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    db.commit()
    db.refresh(base)
    return WorkRead.model_validate(base).model_copy(
        update={"has_reversible_shadow": has_reversible_shadow(db, base.id)}
    )


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


class WorkTagRead(BaseModel):
    """A tag applied to a paper (id + name + colour), for the detail view's applied-tags list."""

    id: uuid.UUID
    name: str
    color: str | None = None

    model_config = {"from_attributes": True}


@router.get("/{work_id}/tags", response_model=list[WorkTagRead])
def list_work_tags(
    work_id: uuid.UUID, db: Session = DB_DEP, actor: User = AUTH_DEP
) -> list[WorkTagRead]:
    """Return the tags applied to this paper (SEE-safe: 404 if the caller can't see the paper).

    Tags are global (not access-scoped), but which paper they hang on is — so this is guarded like
    ``get_work``, hiding a paper's tags from anyone who can't see the paper itself.
    """
    work = db.get(Work, work_id)
    if work is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found")
    _guard_see_work(db, actor, work)
    tags = list(
        db.scalars(
            select(Tag)
            .join(TagLink, TagLink.tag_id == Tag.id)
            .where(TagLink.entity_type == "work", TagLink.entity_id == work_id)
            .order_by(Tag.name)
        ).all()
    )
    return [WorkTagRead.model_validate(tag) for tag in tags]


@router.get("/{work_id}", response_model=WorkRead)
def get_work(work_id: uuid.UUID, db: Session = DB_DEP, actor: User = AUTH_DEP) -> WorkRead:
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
    return WorkRead.model_validate(work).model_copy(
        update={"has_reversible_shadow": has_reversible_shadow(db, work_id)}
    )


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
    if updates:
        record_event(
            db,
            "paper.metadata_edited",
            actor_user_id=actor.id,
            entity_type="work",
            entity_id=str(work.id),
            details={"fields": sorted(updates.keys())},
        )
    _commit_or_doi_409(db)
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
    # Unlink this work's citation edges, then prune canonical references nobody else cites.
    orphan_candidates = set(
        db.scalars(
            select(ReferenceCitation.reference_id).where(
                ReferenceCitation.citing_work_id == work_id
            )
        ).all()
    )
    db.execute(delete(ReferenceCitation).where(ReferenceCitation.citing_work_id == work_id))
    if orphan_candidates:
        still_linked = set(
            db.scalars(
                select(ReferenceCitation.reference_id).where(
                    ReferenceCitation.reference_id.in_(orphan_candidates)
                )
            ).all()
        )
        orphaned = orphan_candidates - still_linked
        if orphaned:
            db.execute(delete(Reference).where(Reference.id.in_(orphaned)))
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
    # References in *other* works that resolved to this one must be re-resolved now it's gone.
    # Capture them and clear the link (also clears any confirmed lock — the target no longer
    # exists); after the work is deleted below we re-run the matcher so each lands at its correct
    # new status (external, or a fresh local_match if a duplicate still covers it), keeping the
    # stored resolution accurate instead of leaving a stale one until the next rescan.
    orphaned_refs = list(
        db.scalars(
            select(Reference).where(
                or_(
                    Reference.resolved_work_id == work_id,
                    # A soft "likely local" guess pointing here would otherwise survive as a
                    # stale likely_match with a NULLed suggestion (FK SET NULL) — re-resolve too.
                    Reference.suggested_work_id == work_id,
                )
            )
        ).all()
    )
    for ref in orphaned_refs:
        ref.resolved_work_id = None
        ref.suggested_work_id = None
        ref.match_score = None
        ref.resolution_status = "unresolved"
    db.execute(
        update(CitationMention)
        .where(CitationMention.resolved_cited_work_id == work_id)
        .values(resolved_cited_work_id=None)
    )

    # Incoming external citations: link rows cascade with the work, but the shared ExternalPaper
    # rows they pointed at would linger until some other work's refetch GCs them — prune the ones
    # whose ONLY link was this work now. Papers the matcher had resolved to this work get their
    # FK nulled by ON DELETE SET NULL (they stay valid citers of other works).
    from app.models.external_citation import ExternalCitationLink, ExternalPaper

    linked_paper_ids = set(
        db.scalars(
            select(ExternalCitationLink.external_paper_id).where(
                ExternalCitationLink.work_id == work_id
            )
        ).all()
    )
    if linked_paper_ids:
        still_linked_papers = set(
            db.scalars(
                select(ExternalCitationLink.external_paper_id).where(
                    ExternalCitationLink.external_paper_id.in_(linked_paper_ids),
                    ExternalCitationLink.work_id != work_id,
                )
            ).all()
        )
        orphaned_papers = linked_paper_ids - still_linked_papers
        if orphaned_papers:
            db.execute(delete(ExternalPaper).where(ExternalPaper.id.in_(orphaned_papers)))

    # B6: drop any local-agent file that created/owns this paper as a stub, so deleting the paper on
    # the server makes it vanish from the agent's server view and a "Reconcile with server" un-indexes
    # it locally (the FK is SET NULL, but we want the row gone, not just detached).
    db.execute(delete(AgentFile).where(AgentFile.work_id == work_id))

    db.delete(work)
    # Re-resolve the detached references now the work is gone (flush first so the matcher's
    # candidate queries can't re-link to the row being deleted). Uses the current fuzzy toggle.
    if orphaned_refs:
        db.flush()
        run_matching_for_references(db, orphaned_refs, accept_policy=effective_accept_policy(db))
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
    # 0-100 similarity between the conflicting values (lowest pairwise, after normalizing
    # whitespace/hyphenation/case) so the UI can show how alike two values are. None when
    # there is no conflict. Values differing only by formatting score ~100.
    match_pct: float | None = None
    assertions: list[MetadataAssertionRead]


class ConfirmFieldRequest(BaseModel):
    field_name: str
    confirmed: bool = True


class SelectAssertion(BaseModel):
    assertion_id: uuid.UUID


class SetMetadataValue(BaseModel):
    field_name: str
    value: str


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


# Enumerated annotation kinds (SPECIFICATION.md §8.8.5 / §9.3). Constrained at the API so invalid
# types are rejected with 422 rather than persisted as free-form strings.
AnnotationType = Literal["highlight", "note", "page_anchor", "citation_note", "tag_note"]


class AnnotationCreate(BaseModel):
    annotation_type: AnnotationType
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
    annotation_type: AnnotationType
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
    # Provider provenance (Phase B2). On a freshly-generated summary these say what was requested
    # vs what actually ran and whether it degraded to the extractive fallback; stored summaries
    # (listed later) carry the persisted ``model_name``, so these default to a non-degraded view.
    provider_requested: str | None = None
    provider_used: str | None = None
    fallback: bool = False
    fallback_reason: str | None = None
    content_hash: str | None = None
    created_by_user_id: uuid.UUID | None = None
    params: dict | None = None
    created_at: datetime

    model_config = {"from_attributes": True}

    @field_validator("source_sections", mode="before")
    @classmethod
    def _sections_never_null(cls, value: object) -> object:
        # Legacy rows (pre-provenance migration) store NULL; present them as an empty list.
        return value or []


class ReferenceRead(BaseModel):
    id: uuid.UUID
    title: str | None = None
    raw_citation: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    year: int | None = None
    # Parsed citation authors (batch 12), for display + author-overlap matching. NULL for
    # pre-batch-12 references until re-extraction.
    authors: list[str] | None = None
    resolution_status: str
    resolved_work_id: uuid.UUID | None = None
    # Unconfirmed fuzzy "likely local" candidate (batch 12): the work this reference *probably* is,
    # with its 0-100 title-match score. Never promoted to resolved_work_id while soft — the client
    # renders these as a one-click "likely match" the owner confirms/rejects.
    suggested_work_id: uuid.UUID | None = None
    match_score: float | None = None
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
    references = references_for_work(db, work_id)
    shorthands = _reference_shorthands(db, [ref.id for ref in references])
    return [
        ReferenceRead.model_validate(ref).model_copy(update={"shorthand": shorthands.get(ref.id)})
        for ref in references
    ]


class ReferenceActionRequest(BaseModel):
    """A confirm/reject/import action on a (canonical) reference (batch 12, item #4)."""

    action: Literal["link", "reject", "import"]


@router.patch("/{work_id}/references/{reference_id}", response_model=ReferenceRead)
def act_on_reference(
    work_id: uuid.UUID,
    reference_id: uuid.UUID,
    payload: ReferenceActionRequest,
    db: Session = DB_DEP,
    actor: User = CONTRIBUTOR_DEP,
) -> ReferenceRead:
    """Confirm / reject / import a reference's "likely local" match (batch 12, item #4).

    Because references are canonical (shared across citing works), the action applies to **all**
    citing works at once; ``work_id`` scopes authorization and confirms this paper actually cites the
    reference. Actions:

    * ``link`` — confirm the suggested candidate: ``resolved_work_id = suggested_work_id``, status
      ``confirmed_match`` (LOCKED — a rescan never reverts or re-suggests it).
    * ``reject`` — status ``rejected_match``; the suggestion is kept so a rescan won't re-propose the
      *same* candidate (a different, better one may still surface).
    * ``import`` — create a library work from the reference (the existing from-reference path).
    """
    work = db.get(Work, work_id)
    if work is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found")
    _guard_modify_work(db, actor, work)
    reference = db.get(Reference, reference_id)
    if reference is None or not db.scalar(
        select(ReferenceCitation.id).where(
            ReferenceCitation.reference_id == reference_id,
            ReferenceCitation.citing_work_id == work_id,
        )
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Reference not found for this paper"
        )

    if payload.action == "link":
        if reference.suggested_work_id is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Reference has no suggested match to confirm",
            )
        reference.resolved_work_id = reference.suggested_work_id
        reference.resolution_status = "confirmed_match"
        record_event(
            db,
            "reference.confirm",
            actor_user_id=actor.id,
            entity_type="reference",
            entity_id=str(reference.id),
            details={"work_id": str(reference.resolved_work_id)},
        )
    elif payload.action == "reject":
        reference.resolution_status = "rejected_match"  # keep suggested_work_id/match_score
        record_event(
            db,
            "reference.reject",
            actor_user_id=actor.id,
            entity_type="reference",
            entity_id=str(reference.id),
            details={
                "work_id": str(reference.suggested_work_id) if reference.suggested_work_id else None
            },
        )
    else:  # import
        import_reference_as_work(reference_id=reference_id, db=db, actor=actor)
        db.refresh(reference)

    db.commit()
    db.refresh(reference)
    shorthands = _reference_shorthands(db, [reference.id])
    return ReferenceRead.model_validate(reference).model_copy(
        update={"shorthand": shorthands.get(reference.id)}
    )


class ReferenceRescanResult(BaseModel):
    """Outcome of a reference→library rematch (batch 12)."""

    scanned: int = 0
    changed: int = 0
    queued: bool = False
    job_id: str | None = None


@router.post("/{work_id}/references/rescan", response_model=ReferenceRescanResult)
def rescan_work_references(
    work_id: uuid.UUID, db: Session = DB_DEP, actor: User = CONTRIBUTOR_DEP
) -> ReferenceRescanResult:
    """Re-run reference→library matching for one paper's bibliography (batch 12, D3).

    Synchronous (a single paper's references are few). Respects the batch-12 status rules: a
    confirmed match stays locked; a rejected candidate is not re-proposed. Uses the current
    ``use_fuzzy_match_as_confirmed`` toggle."""
    work = db.get(Work, work_id)
    if work is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found")
    _guard_modify_work(db, actor, work)
    references = references_for_work(db, work_id)
    changed = run_matching_for_references(db, references, accept_policy=effective_accept_policy(db))
    db.commit()
    return ReferenceRescanResult(scanned=len(references), changed=changed)


@router.post("/references/rescan-all", response_model=ReferenceRescanResult)
def rescan_all_references(db: Session = DB_DEP, actor: User = EDITOR_DEP) -> ReferenceRescanResult:
    """Re-run reference→library matching across the WHOLE library (batch 12, D3).

    A full rematch is a minutes-long job at scale, so it is always enqueued on the background worker
    (mirrors ``POST /duplicates/scan``). Audited as a bulk action."""
    from app.workers.queue import enqueue_reference_rescan

    job_id = enqueue_reference_rescan()
    if job_id is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Reference-rescan queue unavailable",
        )
    record_event(
        db,
        "reference.rescan_all",
        actor_user_id=actor.id,
        entity_type="library",
        details={"job_id": job_id},
    )
    db.commit()
    return ReferenceRescanResult(queued=True, job_id=job_id)


@router.get("/{work_id}/reference-graph")
def get_reference_graph(
    work_id: uuid.UUID,
    include_ref_edges: bool = False,
    include_citing: bool = False,
    max_external: int = Query(default=50, ge=0, le=500),
    db: Session = DB_DEP,
    actor: User = AUTH_DEP,
) -> dict:
    """Weighted reference graph for a paper (B7): the base paper + one node per reference, coloured
    local vs external, carrying per-section mention counts so the client can size nodes by the
    caller's Profile weights. ``include_ref_edges`` adds local ref→ref citation edges;
    ``include_citing`` adds the fetched external citing papers as incoming nodes (batch10 #8)."""
    from app.services.reference_graph import build_reference_graph

    work = db.get(Work, work_id)
    if work is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found")
    _guard_see_work(db, actor, work)
    return build_reference_graph(
        db,
        work,
        visible_ids=access.visible_work_ids(db, actor),
        include_ref_edges=include_ref_edges,
        include_citing=include_citing,
        max_external=max_external,
    )


class CitingPaperRead(BaseModel):
    id: uuid.UUID
    source: str
    external_id: str | None = None
    title: str | None = None
    authors: str | None = None
    year: int | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    venue: str | None = None
    # The library work this citing paper IS, when the local matcher recognizes it (in-library citer).
    resolved_work_id: uuid.UUID | None = None

    model_config = {"from_attributes": True}


class CitingPapersResponse(BaseModel):
    items: list[CitingPaperRead]
    # Provenance of the cached list.
    source: str | None = None
    fetched_at: datetime | None = None
    # The paper's total external citation count snapshot (may exceed the fetched/capped list).
    citation_count: int | None = None
    citation_count_source: str | None = None


def _citing_papers_response(db: Session, work: Work) -> CitingPapersResponse:
    from app.models.external_citation import ExternalCitationLink, ExternalPaper

    rows = db.execute(
        select(ExternalPaper, ExternalCitationLink.fetched_at)
        .join(ExternalCitationLink, ExternalCitationLink.external_paper_id == ExternalPaper.id)
        .where(ExternalCitationLink.work_id == work.id)
        .order_by(ExternalPaper.year.desc().nullslast(), ExternalPaper.title)
    ).all()
    items = [
        CitingPaperRead(
            id=paper.id,
            source=paper.source,
            external_id=paper.external_id,
            title=paper.title,
            authors=paper.authors,
            year=paper.year,
            doi=paper.doi,
            venue=paper.venue,
            resolved_work_id=paper.resolved_work_id,
        )
        for paper, _fetched in rows
    ]
    return CitingPapersResponse(
        items=items,
        # Prefer the per-work snapshot (S12): it survives an authoritative-zero replace, where
        # there are no link rows to carry a source/timestamp. Older rows predate the snapshot.
        source=work.citing_fetched_source or (rows[0][0].source if rows else None),
        fetched_at=work.citing_fetched_at or (rows[0][1] if rows else None),
        citation_count=work.citation_count,
        citation_count_source=work.citation_count_source,
    )


@router.get("/{work_id}/citing-papers", response_model=CitingPapersResponse)
def get_citing_papers(
    work_id: uuid.UUID, db: Session = DB_DEP, actor: User = AUTH_DEP
) -> CitingPapersResponse:
    """Return the cached external papers that cite this work (batch10 #8). Read-only."""
    work = db.get(Work, work_id)
    if work is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found")
    _guard_see_work(db, actor, work)
    return _citing_papers_response(db, work)


@router.post("/{work_id}/citing-papers/fetch", response_model=CitingPapersResponse)
def fetch_citing_papers_endpoint(
    work_id: uuid.UUID, db: Session = DB_DEP, actor: User = CONTRIBUTOR_DEP
) -> CitingPapersResponse:
    """Fetch (or refresh) this work's citing papers from OpenAlex, falling back to Semantic Scholar.

    On-demand and capped (batch10 #8). Needs a DOI (preferred) or arXiv id on the paper; 400 if
    neither is present. Replaces the cached list. Requires modify permission (it writes + calls out).
    """
    from app.services import citing_papers as cp

    work = db.get(Work, work_id)
    if work is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found")
    _guard_modify_work(db, actor, work)
    if not work.doi and not work.arxiv_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Add a DOI or arXiv id to fetch citing papers",
        )
    papers, source, total = cp.fetch_citing_papers(
        doi=work.doi,
        arxiv=work.arxiv_id,
        limit=effective_citing_papers_fetch_cap(db),
    )
    if source is not None:
        cp.store_citing_papers(db, work=work, papers=papers, source=source, total=total)
        record_event(
            db,
            "citations.citing_fetched",
            actor_user_id=actor.id,
            entity_type="work",
            entity_id=str(work_id),
            details={"source": source, "count": len(papers), "total": total},
        )
        db.commit()
    return _citing_papers_response(db, work)


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
    # False when the upload intended extraction but the queue was unreachable (Redis down); the
    # file keeps its owed marker and the recovery sweep retries (D7). True for plain file listings.
    extraction_queued: bool = True
    # How many OTHER papers this exact PDF (same sha256) is also attached to — drives the
    # "duplicate PDF" badge so a deduped attach is visible. Search the hash to find those papers.
    also_in_count: int = 0

    model_config = {"from_attributes": True}


# File statuses for which the original PDF bytes are deliberately not kept on the server.
_PDF_DISCARDED_STATUSES = {"extracted_discarded"}


def _file_content_available(db: Session, file: File) -> bool:
    """Return True if the PDF bytes for ``file`` can be streamed from disk right now."""
    if file.status in _PDF_DISCARDED_STATUSES:
        return False
    try:
        # Derived-aware: a searchable OCR copy counts as streamable content even if it is served
        # in place of the original.
        path = resolve_streamable_pdf_path(db, file=file, settings=get_settings())
    except FileLocationError:
        return False
    return path.exists() and path.is_file()


def _file_read(db: Session, file: File, *, also_in_count: int = 0) -> WorkFileRead:
    return WorkFileRead.model_validate(file).model_copy(
        update={
            "content_available": _file_content_available(db, file),
            "also_in_count": also_in_count,
        }
    )


def _other_work_counts(
    db: Session, file_ids: list[uuid.UUID], this_work_id: uuid.UUID
) -> dict[uuid.UUID, int]:
    """For each file, count the OTHER works it is linked to (excluding ``this_work_id``). One query."""
    if not file_ids:
        return {}
    rows = db.execute(
        select(FileWorkLink.file_id, func.count())
        .where(FileWorkLink.file_id.in_(file_ids), FileWorkLink.work_id != this_work_id)
        .group_by(FileWorkLink.file_id)
    ).all()
    return {file_id: int(count) for file_id, count in rows}


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
    others = _other_work_counts(db, [f.id for f in files], work_id)
    return [_file_read(db, file, also_in_count=others.get(file.id, 0)) for file in files]


@router.post("/{work_id}/files", response_model=WorkFileRead, status_code=status.HTTP_201_CREATED)
def upload_work_file(
    work_id: uuid.UUID,
    file: UploadFile,
    db: Session = DB_DEP,
    actor: User = CONTRIBUTOR_DEP,
) -> WorkFileRead:
    """Upload a PDF and attach it to an existing work (so a manual work isn't a dead end)."""
    assert_queue_has_capacity(db)  # D39: reject before reading the upload when the queue is full
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
    pdf_bytes = file.file.read(_MAX_UPLOAD_BYTES + 1)
    if len(pdf_bytes) > _MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="Uploaded file exceeds 200 MB limit",
        )
    if len(pdf_bytes) < 4 or pdf_bytes[:4] != b"%PDF":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file is not a valid PDF"
        )
    pdf_error = probe_pdf_openable(pdf_bytes)  # E2: reject encrypted/unopenable before any worker
    if pdf_error is not None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=pdf_error)
    file_obj, created_file, _linked = attach_uploaded_pdf_to_work(
        db, work=work, filename=file.filename or "upload.pdf", pdf_bytes=pdf_bytes, actor=actor
    )
    # If this is a deduped attach of an already-extracted PDF, don't re-run extraction: the file's
    # extraction is keyed by file id and would only (re)write the file's original paper, not this one
    # (a known limitation, surfaced by the "duplicate PDF" badge). Skipping avoids a misleading job.
    already_extracted = not created_file and file_obj.status == "extracted"
    extraction_queued = True
    if not already_extracted:
        mark_extraction_requested(file_obj)  # owed marker in the same commit (D7)
    db.commit()
    db.refresh(file_obj)
    if not already_extracted:
        extraction_queued = enqueue_extraction(file_obj.id) is not None
    also_in = _other_work_counts(db, [file_obj.id], work_id).get(file_obj.id, 0)
    return _file_read(db, file_obj, also_in_count=also_in).model_copy(
        update={"extraction_queued": extraction_queued}
    )


def _require_attached_file(db: Session, work_id: uuid.UUID, file_id: uuid.UUID) -> FileWorkLink:
    link = db.scalar(
        select(FileWorkLink).where(FileWorkLink.work_id == work_id, FileWorkLink.file_id == file_id)
    )
    if link is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="File is not attached to this paper"
        )
    return link


@router.put("/{work_id}/main-file/{file_id}", response_model=WorkRead)
def set_main_file(
    work_id: uuid.UUID, file_id: uuid.UUID, db: Session = DB_DEP, actor: User = CONTRIBUTOR_DEP
) -> Work:
    """Choose a paper's primary file for one-click 'Read' (#16). The file must be attached."""
    work = db.get(Work, work_id)
    if work is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found")
    _guard_modify_work(db, actor, work)
    _require_attached_file(db, work_id, file_id)
    work.main_file_id = file_id
    db.commit()
    db.refresh(work)
    return work


@router.delete("/{work_id}/files/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_work_file(
    work_id: uuid.UUID, file_id: uuid.UUID, db: Session = DB_DEP, actor: User = CONTRIBUTOR_DEP
) -> None:
    """Detach a file from a paper (#17). The File itself is retained (it may back other papers);
    only the link is removed. If it was the paper's main file, the pointer is cleared."""
    work = db.get(Work, work_id)
    if work is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found")
    _guard_modify_work(db, actor, work)
    link = _require_attached_file(db, work_id, file_id)
    db.delete(link)
    if work.main_file_id == file_id:
        work.main_file_id = None
    db.commit()


class MoveFileRequest(BaseModel):
    target_work_id: uuid.UUID


@router.post("/{work_id}/files/{file_id}/move", response_model=WorkFileRead)
def move_work_file(
    work_id: uuid.UUID,
    file_id: uuid.UUID,
    payload: MoveFileRequest,
    db: Session = DB_DEP,
    actor: User = CONTRIBUTOR_DEP,
) -> WorkFileRead:
    """Move an attached PDF from one paper to another (issue 4).

    Re-points the file's ``FileWorkLink`` from the source paper to the target: the target gains the
    file, the source loses it. The content-addressed ``File`` bytes are untouched. If the file was
    the source's main file that pointer is cleared; if the target has no main file yet, the moved
    file becomes it. Lets a user consolidate two records (e.g. a stub + a fully-extracted paper)
    without deleting and re-uploading the PDF. Requires modify rights on *both* papers.
    """
    if payload.target_work_id == work_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The file is already attached to this paper",
        )
    source = db.get(Work, work_id)
    if source is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found")
    _guard_modify_work(db, actor, source)
    target = db.get(Work, payload.target_work_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Target paper not found")
    _guard_modify_work(db, actor, target)
    link = _require_attached_file(db, work_id, file_id)

    # If the target already links this file, dropping the source link is the whole move; otherwise
    # re-point the existing link. Clear the work-specific version pointer (segments are file-scoped,
    # so they stay valid across the move).
    already_on_target = db.scalar(
        select(FileWorkLink).where(
            FileWorkLink.work_id == target.id, FileWorkLink.file_id == file_id
        )
    )
    if already_on_target is None:
        link.work_id = target.id
        link.version_id = None
    else:
        db.delete(link)
    if source.main_file_id == file_id:
        source.main_file_id = None
    if target.main_file_id is None:
        target.main_file_id = file_id
    db.commit()
    file_obj = db.get(File, file_id)
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
    # Never surface annotations attached to a merged shadow (Batch D), for anyone.
    stmt = stmt.where(Annotation.work_id.in_(select(Work.id).where(Work.merged_into_id.is_(None))))
    visible = access.visible_work_ids(db, actor)
    if visible is not None:
        stmt = stmt.where(Annotation.work_id.in_(visible))
    stmt = stmt.order_by(Annotation.created_at.desc()).limit(limit)
    return list(db.scalars(stmt).all())


@router.get("/{work_id}/annotations/export")
def export_work_annotations(
    work_id: uuid.UUID,
    output_format: str = Query(
        default="markdown", pattern="^(markdown|text|json)$", alias="format"
    ),
    db: Session = DB_DEP,
    actor: User = AUTH_DEP,
) -> dict[str, str]:
    """Export a work's annotations as Markdown, plain text, or JSON (SPEC §8.8.7, §8.17.4)."""
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
    if output_format == "json":
        payload = {
            "work": {"id": str(work_id), "title": title},
            "annotations": [
                {
                    "page": a.page,
                    "type": a.annotation_type,
                    "coordinates": a.coordinates,
                    "selected_text": a.selected_text,
                    "note": a.content_markdown,
                    "created_at": a.created_at.isoformat() if a.created_at else None,
                    "author": str(a.created_by_user_id) if a.created_by_user_id else None,
                }
                for a in rows
            ],
        }
        return {
            "filename": f"annotations-{work_id}.json",
            "content_type": "application/json",
            "content": json.dumps(payload, ensure_ascii=False, indent=2),
        }
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
    db.flush()
    record_event(
        db,
        "annotation.created",
        actor_user_id=actor.id,
        entity_type="annotation",
        entity_id=str(annotation.id),
        details={"work_id": str(work_id), "annotation_type": annotation.annotation_type},
    )
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
    summary_type = payload.summary_type
    if summary_type == "auto":
        # Mirror the scope-summary resolution (ai.py): use the configured provider — the local LLM
        # when selected, otherwise the deterministic extractive engine — so the paper-view
        # "Summarise" action does the right thing without the caller knowing the AI config.
        from app.services.ai_config import get_ai_config  # noqa: PLC0415 (avoid import cycle)

        ai_cfg = get_ai_config(db)
        summary_type = "local_llm" if ai_cfg.summary_provider == "local_llm" else "extractive"
    try:
        summary = summarize_work(
            db,
            work,
            summary_type=summary_type,
            max_sentences=payload.max_sentences,
            model_name=payload.model_name,
            created_by_user_id=actor.id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    # Serialize *before* commit: transient provenance attrs (provider_used/fallback/…) attached by
    # summarize_work would be lost on the post-commit attribute expiry.
    result = SummaryRead.model_validate(summary)
    db.commit()
    return result


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


def _conflict_match_pct(distinct_values: list[str]) -> float:
    """Lowest pairwise similarity (0-100) among the distinct conflicting values.

    For the common two-value conflict this is just the similarity between the two values;
    for more it reports the most divergent pair, so a low number always means "these really
    differ" rather than "one happens to match".
    """
    return round(
        min(
            similarity_pct(a, b)
            for i, a in enumerate(distinct_values)
            for b in distinct_values[i + 1 :]
        ),
        1,
    )


def _work_field_reviews(db: Session, work: Work) -> list[FieldReview]:
    """Build the per-field metadata review (assertions grouped by field, conflicts flagged)."""
    confirmed = set(work.confirmed_fields or [])
    rows = db.scalars(
        select(MetadataAssertion)
        .where(MetadataAssertion.entity_type == "work", MetadataAssertion.entity_id == work.id)
        .order_by(MetadataAssertion.field_name, MetadataAssertion.retrieved_at)
    ).all()
    by_field: dict[str, list[MetadataAssertion]] = {}
    for assertion in rows:
        by_field.setdefault(assertion.field_name, []).append(assertion)
    reviews: list[FieldReview] = []
    for field_name, assertions in sorted(by_field.items()):
        canonical = next((a.value for a in assertions if a.selected_as_canonical), None)
        distinct = list(dict.fromkeys(a.value for a in assertions))
        has_conflict = len(distinct) > 1
        match_pct = _conflict_match_pct(distinct) if has_conflict else None
        reviews.append(
            FieldReview(
                field_name=field_name,
                canonical_value=canonical,
                has_conflict=has_conflict,
                confirmed=field_name in confirmed,
                match_pct=match_pct,
                assertions=assertions,
            )
        )
    return reviews


@router.get("/{work_id}/metadata", response_model=list[FieldReview])
def get_work_metadata(
    work_id: uuid.UUID, db: Session = DB_DEP, actor: User = AUTH_DEP
) -> list[FieldReview]:
    """Return metadata assertions for a work, grouped by field, flagging conflicts."""
    work = db.get(Work, work_id)
    if work is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found")
    _guard_see_work(db, actor, work)
    return _work_field_reviews(db, work)


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
    assert_queue_has_capacity(db)  # D39: reject when the processing queue is full
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
    # Persist the owed marker for every file first (D7): even if the enqueue below fails on a dead
    # queue, the recovery sweep re-enqueues these on the next startup.
    for file_id in file_ids:
        file = db.get(File, file_id)
        if file is not None:
            mark_extraction_requested(file)
    db.commit()
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


@router.post("/{work_id}/citing-papers/fetch-job", status_code=status.HTTP_202_ACCEPTED)
def fetch_citing_papers_job_endpoint(
    work_id: uuid.UUID,
    db: Session = DB_DEP,
    actor: User = CONTRIBUTOR_DEP,
) -> dict[str, str | None]:
    """Queue a background citing-papers fetch for a work (the Library batch action).

    Same permission/precondition rules as the inline fetch: modify permission and a DOI or arXiv
    id are required; the worker replaces the cached citing list when a source answers.
    """
    work = db.get(Work, work_id)
    if work is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found")
    _guard_modify_work(db, actor, work)
    if not work.doi and not work.arxiv_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Add a DOI or arXiv id to fetch citing papers",
        )
    job_id = enqueue_citing_fetch(work_id)
    if job_id is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Job queue unavailable"
        )
    return {"job_id": job_id, "status": "queued"}


@router.post("/{work_id}/summaries/job", status_code=status.HTTP_202_ACCEPTED)
def summarize_work_job_endpoint(
    work_id: uuid.UUID,
    db: Session = DB_DEP,
    actor: User = CONTRIBUTOR_DEP,
) -> dict[str, str | None]:
    """Queue a background per-paper summary (the Library batch action).

    Uses the same "auto" provider resolution as the inline create-summary endpoint (local LLM when
    configured, else extractive).
    """
    work = db.get(Work, work_id)
    if work is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found")
    _guard_modify_work(db, actor, work)
    job_id = enqueue_work_summary(work_id)
    if job_id is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Job queue unavailable"
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
    arxiv_id: str | None = None
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
    # The candidate's identifiers, carried through so the work's empty arxiv_id/doi get backfilled
    # when the PDF is attached (find-on-web previously dropped them).
    doi: str | None = None
    arxiv_id: str | None = None


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
        arxiv_id=candidate.arxiv_id,
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
            doi=item.doi,
            arxiv_id=item.arxiv_id,
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


class WebFindApplyMetadataRequest(BaseModel):
    """A Find-on-web result's metadata to record as candidate assertions (issue 9)."""

    source: str = "web"
    title: str | None = None
    abstract: str | None = None
    authors: list[str] = []
    year: int | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    venue: str | None = None


@router.post("/{work_id}/find-on-web/apply-metadata", response_model=list[FieldReview])
def find_on_web_apply_metadata_endpoint(
    work_id: uuid.UUID,
    payload: WebFindApplyMetadataRequest,
    db: Session = DB_DEP,
    actor: User = CONTRIBUTOR_DEP,
) -> list[FieldReview]:
    """Record a Find-on-web result's metadata as candidate assertions for review (issue 9).

    Adds the fetched values under a ``web_find:<source>`` provenance source through the same path as
    external enrichment: a non-trusted source never silently overwrites, so the values surface as
    candidates the user promotes with "Use this". Returns the refreshed per-field review so the paper
    view can show the new candidates immediately. arXiv id (which has no review row) backfills any
    empty, unlocked ``arxiv_id``.
    """
    from app.services.identifiers import backfill_identifiers
    from app.services.metadata_enrichment import ExternalMetadata, apply_external_metadata

    work = db.get(Work, work_id)
    if work is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found")
    _guard_modify_work(db, actor, work)
    meta = ExternalMetadata(
        source=f"web_find:{payload.source}",
        title=payload.title,
        abstract=payload.abstract,
        authors=payload.authors,
        doi=payload.doi,
        arxiv_id=payload.arxiv_id,
        year=payload.year,
        venue=payload.venue,
    )
    apply_external_metadata(db, work, meta)
    backfill_identifiers(work, arxiv_id=payload.arxiv_id)  # doi stays a reviewable candidate
    work.updated_at = datetime.now(UTC)
    record_event(
        db,
        "web_find.metadata_applied",
        actor_user_id=actor.id,
        entity_type="work",
        entity_id=str(work_id),
        details={"source": meta.source},
    )
    _commit_or_doi_409(db)
    return _work_field_reviews(db, work)


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
    _commit_or_doi_409(db)
    db.refresh(work)
    return work


@router.post("/{work_id}/metadata/set", response_model=list[FieldReview])
def set_metadata_value(
    work_id: uuid.UUID,
    payload: SetMetadataValue,
    db: Session = DB_DEP,
    actor: User = CONTRIBUTOR_DEP,
) -> list[FieldReview]:
    """Set a metadata field to a user-entered value (a manual correction).

    Records a ``source="user"`` assertion, promotes it to canonical, and locks the field
    (``confirmed_fields``) so later enrichment/extraction can't silently overwrite it (AGENTS.md
    rule 5). For a promotable Work column (title/abstract/year/venue/doi) the Work is updated too;
    for fields with no dedicated column (e.g. ``authors``) the value lives purely as the canonical
    assertion, which is how the UI already reads it. An empty value clears the field: existing
    assertions are de-canonicalised and the field is unlocked (no new assertion is written).
    """
    field = payload.field_name.strip()
    value = payload.value.strip()
    if not field:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="field_name is required"
        )
    work = db.get(Work, work_id)
    if work is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found")
    _guard_modify_work(db, actor, work)
    # A manual value always wins its field: drop any prior canonical flag for the field first.
    db.execute(
        update(MetadataAssertion)
        .where(
            MetadataAssertion.entity_type == "work",
            MetadataAssertion.entity_id == work_id,
            MetadataAssertion.field_name == field,
        )
        .values(selected_as_canonical=False)
    )
    locked = set(work.confirmed_fields or [])
    if value:
        db.add(
            MetadataAssertion(
                entity_type="work",
                entity_id=work_id,
                field_name=field,
                value=value,
                source="user",
                confidence=1.0,
                selected_as_canonical=True,
            )
        )
        if field in _PROMOTABLE_FIELDS:
            _apply_assertion_to_work(work, field, value, "user")
        locked.add(field)  # lock so enrichment won't clobber the manual value
    else:
        locked.discard(field)  # cleared → unlock; leaves the field with no canonical value
    work.confirmed_fields = sorted(locked)
    work.updated_at = datetime.now(UTC)
    record_event(
        db,
        "metadata.value_set",
        actor_user_id=actor.id,
        entity_type="work",
        entity_id=str(work_id),
        details={"field": field, "cleared": not value},
    )
    _commit_or_doi_409(db)
    db.refresh(work)
    return _work_field_reviews(db, work)


def _choose_best_assertion(assertions: list[MetadataAssertion]) -> MetadataAssertion:
    """Pick the preferred assertion for a field (issue 3 bulk apply): a GROBID value wins; else the
    current canonical; else the most recently retrieved."""
    grobid = [a for a in assertions if a.source == "grobid"]
    if grobid:
        return max(grobid, key=lambda a: a.retrieved_at)
    canonical = next((a for a in assertions if a.selected_as_canonical), None)
    return canonical or max(assertions, key=lambda a: a.retrieved_at)


class BulkApplyMetadataRequest(BaseModel):
    work_ids: list[uuid.UUID]
    field_name: str  # one of _PROMOTABLE_FIELDS, or "all" for every promotable field


def _promote_best_field(db: Session, work: Work, field_name: str) -> bool:
    """Promote the preferred assertion for ``field_name`` on ``work`` to canonical and apply it.

    Returns True if a value was applied, False if skipped (field locked/confirmed or no assertion).
    Caller is responsible for the modify-permission guard and the commit.
    """
    if field_name in set(work.confirmed_fields or []):
        return False  # locked/confirmed — never clobber in bulk
    assertions = list(
        db.scalars(
            select(MetadataAssertion).where(
                MetadataAssertion.entity_type == "work",
                MetadataAssertion.entity_id == work.id,
                MetadataAssertion.field_name == field_name,
            )
        ).all()
    )
    if not assertions:
        return False
    chosen = _choose_best_assertion(assertions)
    db.execute(
        update(MetadataAssertion)
        .where(
            MetadataAssertion.entity_type == "work",
            MetadataAssertion.entity_id == work.id,
            MetadataAssertion.field_name == field_name,
        )
        .values(selected_as_canonical=False)
    )
    chosen.selected_as_canonical = True
    _apply_assertion_to_work(work, field_name, chosen.value, chosen.source)
    return True


@router.post("/bulk-apply-metadata")
def bulk_apply_best_metadata(
    payload: BulkApplyMetadataRequest,
    db: Session = DB_DEP,
    actor: User = CONTRIBUTOR_DEP,
) -> dict[str, int | str]:
    """Set metadata from the best available source across many selected papers (issue 3).

    ``field_name`` is one of the promotable fields, or ``"all"`` to run every promotable field.
    For each selected paper independently, promote the preferred assertion (GROBID value preferred,
    else current canonical, else most recent) to canonical and apply it — the same effect as clicking
    "Use this" per paper. Fields already user-confirmed (locked) are skipped so a bulk action never
    silently overwrites a corrected value (AGENTS.md rule 5); papers the caller can't modify, or with
    no assertion for a field, are skipped too. With ``"all"``, a paper counts as applied if at least
    one of its fields was promoted.
    """
    if payload.field_name != "all" and payload.field_name not in _PROMOTABLE_FIELDS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"field_name must be 'all' or one of {sorted(_PROMOTABLE_FIELDS)}",
        )
    fields = sorted(_PROMOTABLE_FIELDS) if payload.field_name == "all" else [payload.field_name]
    applied = 0
    skipped = 0
    for work_id in payload.work_ids:
        work = db.get(Work, work_id)
        if work is None:
            skipped += 1
            continue
        try:
            _guard_modify_work(db, actor, work)
        except HTTPException:
            skipped += 1  # not the caller's to modify
            continue
        applied_any = False
        for field in fields:
            if _promote_best_field(db, work, field):
                applied_any = True
        if applied_any:
            work.updated_at = datetime.now(UTC)
            applied += 1
        else:
            skipped += 1
    if applied:
        record_event(
            db,
            "metadata.bulk_applied",
            actor_user_id=actor.id,
            entity_type="work",
            entity_id="bulk",
            details={"field": payload.field_name, "applied": applied, "skipped": skipped},
        )
    _commit_or_doi_409(db)
    return {"field_name": payload.field_name, "applied": applied, "skipped": skipped}


@router.delete("/{work_id}/metadata/{assertion_id}", response_model=WorkRead)
def delete_metadata_assertion(
    work_id: uuid.UUID,
    assertion_id: uuid.UUID,
    db: Session = DB_DEP,
    actor: User = CONTRIBUTOR_DEP,
) -> Work:
    """Delete one metadata assertion so a user can resolve a conflict by removing wrong entries.

    404 if the assertion is missing or belongs to a different paper. Gated like the other metadata
    mutations (contributor floor + own-only ``_guard_modify_work``).

    Canonical re-resolution: if the deleted assertion was the field's canonical one, the most
    recently retrieved of the *remaining* assertions for that field is promoted to canonical (and
    applied to the Work for promotable fields), mirroring ``select_metadata_assertion``. If no
    assertions remain for the field, nothing is left canonical and the Work's column value is kept
    as-is (we never blank out an existing title/abstract just because its provenance rows are gone).
    """
    work = db.get(Work, work_id)
    if work is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found")
    _guard_modify_work(db, actor, work)
    assertion = db.get(MetadataAssertion, assertion_id)
    if assertion is None or assertion.entity_type != "work" or assertion.entity_id != work_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Assertion not found")

    field_name = assertion.field_name
    was_canonical = assertion.selected_as_canonical
    db.delete(assertion)
    db.flush()

    if was_canonical:
        # Re-pick a canonical from the remaining assertions of this field (newest retrieved), or
        # leave the field with no canonical if none remain.
        replacement = db.scalars(
            select(MetadataAssertion)
            .where(
                MetadataAssertion.entity_type == "work",
                MetadataAssertion.entity_id == work_id,
                MetadataAssertion.field_name == field_name,
            )
            .order_by(MetadataAssertion.retrieved_at.desc())
        ).first()
        if replacement is not None:
            replacement.selected_as_canonical = True
            if field_name in _PROMOTABLE_FIELDS:
                _apply_assertion_to_work(work, field_name, replacement.value, replacement.source)
    work.updated_at = datetime.now(UTC)
    _commit_or_doi_409(db)
    db.refresh(work)
    return work


class NeighborhoodNodeRead(BaseModel):
    # Mirrors app.services.citation_graph.GraphNode (kept local to avoid a works<->graph import
    # cycle). Same shape as the citation-graph endpoint's node so the frontend reuses one type.
    id: str
    label: str
    type: str
    work_id: uuid.UUID | None = None
    year: int | None = None
    doi: str | None = None
    degree: int = 0
    pagerank: float = 0.0
    betweenness: float = 0.0
    color_group: str | None = None
    warning: bool = False


class NeighborhoodEdgeRead(BaseModel):
    source: str
    target: str
    weight: int
    resolution: str


class CitationNeighborhoodResponse(BaseModel):
    nodes: list[NeighborhoodNodeRead]
    edges: list[NeighborhoodEdgeRead]
    summary: dict


@router.get("/{work_id}/citation-neighborhood", response_model=CitationNeighborhoodResponse)
def citation_neighborhood(
    work_id: uuid.UUID,
    hops: int = Query(1, ge=1, le=3),
    node_mode: Literal["local_only", "include_external"] = Query("local_only"),
    color_by: Literal["none", "shelf", "tag", "topic", "status"] = Query("none"),
    db: Session = DB_DEP,
    actor: User = AUTH_DEP,
) -> CitationNeighborhoodResponse:
    """Local citation neighborhood (``hops`` steps, default 1) around one focus paper (§8.9).

    A "focus on this paper" graph payload: the focus work plus the works it cites / that cite it, out
    to ``hops`` steps, as the same shape the citation-graph endpoint returns (centrality + color +
    warning encodings included). Access control: 404 unless the caller may see the focus work, and
    the neighborhood is clamped to the caller's visible works, so a hidden paper never surfaces.
    """
    work = db.get(Work, work_id)
    if work is None or not access.can_see_work(db, actor, work):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found")
    graph = build_citation_neighborhood(
        db,
        work_id=work_id,
        hops=hops,
        node_mode=node_mode,
        color_by=color_by,
        visible_ids=access.visible_work_ids(db, actor),
    )
    if graph is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found")
    return CitationNeighborhoodResponse(
        nodes=[NeighborhoodNodeRead(**vars(node)) for node in graph.nodes],
        edges=[NeighborhoodEdgeRead(**vars(edge)) for edge in graph.edges],
        summary=graph.summary,
    )
