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
from app.db.session import get_db
from app.models.user import User
from app.services import access
from app.services.citation_summary import (
    DEFAULT_LIMIT,
    SummaryScope,
    citation_summary,
)
from app.services.saved_filters import (
    get_owned_saved_filter,
    resolve_saved_filter_work_ids,
)

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


class YearCountModel(BaseModel):
    year: int | None = None
    work_count: int


class CitationSummaryResponse(BaseModel):
    scope_work_count: int
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
