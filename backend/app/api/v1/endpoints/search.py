"""Search endpoints (semantic / lexical search + embedding reindex)."""

import uuid
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import require_authenticated_user, require_min_role
from app.core.security import Role
from app.db.session import get_db
from app.models.user import User
from app.services import access
from app.services.embeddings import get_embedding_provider
from app.services.semantic_search import ensure_work_embeddings, semantic_search

router = APIRouter()
DB_DEP = Depends(get_db)
AUTH_DEP = Depends(require_authenticated_user)
EDITOR_DEP = Depends(require_min_role(Role.EDITOR))


class SemanticSearchRequest(BaseModel):
    q: str
    limit: int = 10
    # 'embedding' ranks by vector similarity (read-only); 'lexical' ranks by term overlap.
    mode: Literal["embedding", "lexical"] = "embedding"


class SemanticSearchItem(BaseModel):
    work_id: uuid.UUID
    title: str | None = None
    year: int | None = None
    score: float


class SemanticSearchResponse(BaseModel):
    query: str
    mode: str
    items: list[SemanticSearchItem]


@router.post("/semantic", response_model=SemanticSearchResponse)
def search_semantic(
    payload: SemanticSearchRequest, db: Session = DB_DEP, actor: User = AUTH_DEP
) -> SemanticSearchResponse:
    """Rank works by similarity to a free-text query. Read-only: embeddings are built off this
    path (on import / via /search/reindex), so a search never writes to the database.

    Access control: results are filtered to papers the caller may SEE. We over-fetch then trim so
    the visible result count is preserved when hidden papers rank highly."""
    limit = max(1, min(payload.limit, 50))
    visible = access.visible_work_ids(db, actor)
    # Over-fetch so trimming hidden hits still leaves up to ``limit`` visible results.
    fetch = limit if visible is None else min(limit * 5, 250)
    hits = semantic_search(db, payload.q, limit=fetch, mode=payload.mode)
    if visible is not None:
        hits = [hit for hit in hits if hit.work.id in visible]
    hits = hits[:limit]
    return SemanticSearchResponse(
        query=payload.q,
        mode=payload.mode,
        items=[
            SemanticSearchItem(
                work_id=hit.work.id,
                title=hit.work.canonical_title,
                year=hit.work.year,
                score=round(hit.score, 4),
            )
            for hit in hits
        ],
    )


@router.post("/reindex")
def reindex_embeddings(db: Session = DB_DEP, _: User = EDITOR_DEP) -> dict[str, int | str]:
    """Embed any works missing a vector for the active provider (owner/editor). Returns count added.

    Embeddings are normally created on import in the background; this rebuilds them on demand (e.g.
    after a bulk import while the worker was down, or after switching embedding providers)."""
    added = ensure_work_embeddings(db, provider=get_embedding_provider(db=db))
    db.commit()
    return {"indexed": added, "status": "ok"}
