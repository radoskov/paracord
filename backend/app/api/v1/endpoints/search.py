"""Search endpoints (semantic / vector search)."""

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.semantic_search import semantic_search

router = APIRouter()
DB_DEP = Depends(get_db)


class SemanticSearchRequest(BaseModel):
    q: str
    limit: int = 10


class SemanticSearchItem(BaseModel):
    work_id: uuid.UUID
    title: str | None = None
    year: int | None = None
    score: float


class SemanticSearchResponse(BaseModel):
    query: str
    items: list[SemanticSearchItem]


@router.post("/semantic", response_model=SemanticSearchResponse)
def search_semantic(payload: SemanticSearchRequest, db: Session = DB_DEP) -> SemanticSearchResponse:
    """Rank works by semantic similarity to a free-text query (local embeddings)."""
    limit = max(1, min(payload.limit, 50))
    hits = semantic_search(db, payload.q, limit=limit)
    db.commit()  # persist any embeddings computed on this first pass
    return SemanticSearchResponse(
        query=payload.q,
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
