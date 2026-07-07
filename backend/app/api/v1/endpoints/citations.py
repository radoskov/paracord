"""Citation analytics endpoints (SPEC §8.11, D38 Track C P4).

``GET /citations/summary`` returns the scoped :class:`CitationSummary` — the README-headline
citation analytics (most-cited local / external works, frequently-cited-but-missing works, bridge
papers, isolated papers, chronological distribution). Auth + visibility mirror the citation-graph /
viz endpoints: the actor must SEE the scope container, and every scope is SEE-clamped to the actor's
visible works, so a hidden paper never contributes to (or is named in) a summary. Read-only.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import require_authenticated_user
from app.core.config import get_settings
from app.db.session import get_db
from app.models.citation import Reference
from app.models.user import User
from app.services import access, citation_worklist
from app.services.citation_summary import (
    DEFAULT_LIMIT,
    SummaryScope,
    citation_summary,
)
from app.services.export_service import MISSING_EXPORT_FORMATS, render_missing_works
from app.services.external_preview import external_preview
from app.services.saved_filters import (
    get_owned_saved_filter,
    resolve_saved_filter_work_ids,
)

# Missing-list export (Track C C3b) pulls the full missing set, not just the on-screen top rows.
MISSING_EXPORT_LIMIT = 1000

router = APIRouter()
DB_DEP = Depends(get_db)
AUTH_DEP = Depends(require_authenticated_user)

_ScopeType = Literal[
    "library",
    "shelf",
    "rack",
    "search_result",
    "selected_papers",
    "import_batch",
    "saved_filter",
]


class RankedWorkModel(BaseModel):
    work_id: uuid.UUID
    title: str
    year: int | None = None
    doi: str | None = None
    score: float


class MissingWorkModel(BaseModel):
    key: str
    title: str
    doi: str | None = None
    year: int | None = None
    cited_by_count: int
    mention_count: int
    reference_id: uuid.UUID | None = None
    arxiv_id: str | None = None


class YearCountModel(BaseModel):
    year: int | None = None
    work_count: int


class CitationSummaryResponse(BaseModel):
    scope_work_count: int
    coverage_held: int
    coverage_total: int
    coverage_pct: float | None = None
    most_cited_local: list[RankedWorkModel]
    most_cited_external: list[RankedWorkModel]
    frequently_cited_missing: list[MissingWorkModel]
    bridge_papers: list[RankedWorkModel]
    isolated_papers: list[RankedWorkModel]
    chronological: list[YearCountModel]
    bridge_method: str
    computed_at: datetime
    version: str
    notes: list[str]


@router.get("/summary", response_model=CitationSummaryResponse)
def get_citation_summary(
    scope_type: _ScopeType = Query("library"),
    scope_id: uuid.UUID | None = Query(None),
    work_ids: list[uuid.UUID] | None = Query(None),
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=100),
    db: Session = DB_DEP,
    actor: User = AUTH_DEP,
) -> CitationSummaryResponse:
    """Compute the scoped citation summary (§8.11) for the given scope.

    Access control: a shelf/rack scope requires SEE on that container (404 otherwise); the summary's
    works/references are clamped to the caller's visible works. Cached + versioned by a scope
    signature (see :func:`app.services.citation_summary.citation_summary`).
    """
    if not access.can_see_scope_container(db, actor, scope_type=scope_type, scope_id=scope_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scope not found")

    resolved_work_ids = work_ids
    if scope_type == "saved_filter":
        if scope_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="scope id is required"
            )
        saved = get_owned_saved_filter(db, actor, scope_id)
        if saved is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scope not found")
        resolved_work_ids = resolve_saved_filter_work_ids(db, actor, saved)

    scope = SummaryScope(type=scope_type, id=scope_id, work_ids=resolved_work_ids)
    try:
        summary = citation_summary(db, actor, scope, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return CitationSummaryResponse(
        scope_work_count=summary.scope_work_count,
        coverage_held=summary.coverage_held,
        coverage_total=summary.coverage_total,
        coverage_pct=summary.coverage_pct,
        most_cited_local=[RankedWorkModel(**vars(w)) for w in summary.most_cited_local],
        most_cited_external=[RankedWorkModel(**vars(w)) for w in summary.most_cited_external],
        frequently_cited_missing=[
            MissingWorkModel(**vars(m)) for m in summary.frequently_cited_missing
        ],
        bridge_papers=[RankedWorkModel(**vars(w)) for w in summary.bridge_papers],
        isolated_papers=[RankedWorkModel(**vars(w)) for w in summary.isolated_papers],
        chronological=[YearCountModel(**vars(y)) for y in summary.chronological],
        bridge_method=summary.bridge_method,
        computed_at=summary.computed_at,
        version=summary.version,
        notes=summary.notes,
    )


def _resolve_scope_work_ids(
    db: Session,
    actor: User,
    *,
    scope_type: _ScopeType,
    scope_id: uuid.UUID | None,
    work_ids: list[uuid.UUID] | None,
) -> list[uuid.UUID] | None:
    """SEE-check the scope container and resolve a ``saved_filter`` scope to work ids (404 on miss)."""
    if not access.can_see_scope_container(db, actor, scope_type=scope_type, scope_id=scope_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scope not found")
    if scope_type == "saved_filter":
        if scope_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="scope id is required"
            )
        saved = get_owned_saved_filter(db, actor, scope_id)
        if saved is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scope not found")
        return resolve_saved_filter_work_ids(db, actor, saved)
    return work_ids


# --- external-reference preview (Track C C1) ---------------------------------------------------


class ExternalPreviewResponse(BaseModel):
    """A compact on-demand metadata preview for an external (cited-but-missing) reference."""

    available: bool
    title: str | None = None
    authors: list[str] = []
    year: int | None = None
    venue: str | None = None
    abstract: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    sources: list[str] = []
    message: str | None = None


@router.get("/external-preview", response_model=ExternalPreviewResponse)
def get_external_preview(
    doi: str | None = Query(None),
    arxiv: str | None = Query(None),
    reference_id: uuid.UUID | None = Query(None),
    db: Session = DB_DEP,
    actor: User = AUTH_DEP,
) -> ExternalPreviewResponse:
    """Fetch a metadata preview for a cited-but-missing reference by DOI / arXiv id.

    Identifier-only egress (SPEC §7): only the identifier is sent to the enrichment connectors. When
    ``reference_id`` is given, its citing work must be visible to the actor (404 otherwise) and the
    reference's DOI/arXiv id is used. With no identifier — or when no source returns anything — the
    response is ``available=false`` with a "no preview available" message rather than an error.
    """
    if reference_id is not None:
        reference = db.get(Reference, reference_id)
        visible = access.visible_work_ids(db, actor)
        if reference is None or (visible is not None and reference.citing_work_id not in visible):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Reference not found")
        doi = doi or reference.doi
        arxiv = arxiv or reference.arxiv_id

    if not (doi or arxiv):
        return ExternalPreviewResponse(
            available=False, message="No preview available (no identifier)."
        )

    preview = external_preview(doi=doi, arxiv_id=arxiv, settings=get_settings())
    if preview is None:
        return ExternalPreviewResponse(available=False, message="No preview available.")
    return ExternalPreviewResponse(
        available=True,
        title=preview.title,
        authors=preview.authors,
        year=preview.year,
        venue=preview.venue,
        abstract=preview.abstract,
        doi=preview.doi,
        arxiv_id=preview.arxiv_id,
        sources=preview.sources,
    )


# --- missing-work worklist (Track C C3a) -------------------------------------------------------


class WorklistResponse(BaseModel):
    """A user's recorded import/ignore decisions, keyed by the normalized missing-work key."""

    decisions: dict[str, str]


class WorklistDecisionRequest(BaseModel):
    key: str
    decision: str


@router.get("/worklist", response_model=WorklistResponse)
def get_worklist(db: Session = DB_DEP, actor: User = AUTH_DEP) -> WorklistResponse:
    """Return the caller's frequently-cited-but-missing import/ignore decisions."""
    return WorklistResponse(decisions=citation_worklist.list_decisions(db, actor.id))


@router.put("/worklist", response_model=WorklistResponse)
def put_worklist_decision(
    payload: WorklistDecisionRequest, db: Session = DB_DEP, actor: User = AUTH_DEP
) -> WorklistResponse:
    """Record (upsert) the caller's ``import``/``ignore`` decision for one missing work."""
    try:
        citation_worklist.set_decision(db, actor.id, payload.key, payload.decision)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    db.commit()
    return WorklistResponse(decisions=citation_worklist.list_decisions(db, actor.id))


@router.delete("/worklist", response_model=WorklistResponse)
def delete_worklist_decision(
    key: str = Query(...), db: Session = DB_DEP, actor: User = AUTH_DEP
) -> WorklistResponse:
    """Clear the caller's decision for one missing work (undo)."""
    citation_worklist.clear_decision(db, actor.id, key)
    db.commit()
    return WorklistResponse(decisions=citation_worklist.list_decisions(db, actor.id))


# --- missing-list export (Track C C3b) ---------------------------------------------------------


class MissingExportResponse(BaseModel):
    filename: str
    content_type: str
    content: str


@router.get("/missing-export", response_model=MissingExportResponse)
def export_missing_works(
    scope_type: _ScopeType = Query("library"),
    scope_id: uuid.UUID | None = Query(None),
    work_ids: list[uuid.UUID] | None = Query(None),
    output_format: str = Query("bibtex", alias="format"),
    db: Session = DB_DEP,
    actor: User = AUTH_DEP,
) -> MissingExportResponse:
    """Export the scope's frequently-cited-but-missing works as BibTeX or CSV (Track C C3b)."""
    if output_format not in MISSING_EXPORT_FORMATS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported format: {output_format}",
        )
    resolved_work_ids = _resolve_scope_work_ids(
        db, actor, scope_type=scope_type, scope_id=scope_id, work_ids=work_ids
    )
    scope = SummaryScope(type=scope_type, id=scope_id, work_ids=resolved_work_ids)
    try:
        summary = citation_summary(db, actor, scope, limit=MISSING_EXPORT_LIMIT)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    content = render_missing_works(summary.frequently_cited_missing, output_format)
    extension, content_type = MISSING_EXPORT_FORMATS[output_format]
    return MissingExportResponse(
        filename=f"cited-but-missing.{extension}",
        content_type=content_type,
        content=content,
    )
