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
from app.services.bm25_index import get_index, lexical_search_papers
from app.services.chunk_embeddings import backfill_chunk_embeddings
from app.services.chunk_search import semantic_search_papers
from app.services.embeddings import resolve_embedding_provider
from app.services.hybrid_search import hybrid_search
from app.services.semantic_search import ensure_work_embeddings

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
    # The best-matching passage + its section (chunk-level embedding mode); null in lexical mode
    # and in the document-level fallback.
    passage: str | None = None
    section: str | None = None


class SemanticSearchResponse(BaseModel):
    query: str
    mode: str
    items: list[SemanticSearchItem]
    # Provider provenance (Phase B2): what embedding provider actually served the ranking vs what
    # was configured, so the UI can honestly say "requested X, using Y" when it silently degraded.
    # Null in lexical mode (no embeddings are used).
    embedding_provider_used: str | None = None
    embedding_provider_requested: str | None = None
    degraded: bool = False
    degraded_reason: str | None = None


class HybridSearchRequest(BaseModel):
    q: str
    limit: int = 10
    # 'hybrid' fuses lexical (BM25F+) and semantic (dense) via RRF; the others use one engine.
    mode: Literal["lexical", "semantic", "hybrid"] = "hybrid"


class HybridSearchItem(BaseModel):
    work_id: uuid.UUID
    title: str | None = None
    year: int | None = None
    score: float
    passage: str | None = None
    section: str | None = None
    # Per-engine 1-based ranks (which engine surfaced the paper); null if that engine didn't.
    lexical_rank: int | None = None
    semantic_rank: int | None = None


class HybridSearchResponse(BaseModel):
    query: str
    mode: str
    items: list[HybridSearchItem]
    # Embedding provenance (Phase B2) — populated for semantic/hybrid modes, null for lexical.
    embedding_provider_used: str | None = None
    embedding_provider_requested: str | None = None
    degraded: bool = False
    degraded_reason: str | None = None


@router.post("", response_model=HybridSearchResponse)
def search(
    payload: HybridSearchRequest, db: Session = DB_DEP, actor: User = AUTH_DEP
) -> HybridSearchResponse:
    """Unified search: lexical (BM25F+), semantic (dense), or hybrid (RRF fusion, default).

    Read-only. Access control is applied inside each engine (results are filtered to papers the
    caller may SEE). Semantic/hybrid modes report which embedding provider actually served the
    ranking vs the one configured, so a silent hash-BOW fallback is visible."""
    limit = max(1, min(payload.limit, 50))
    visible = access.visible_work_ids(db, actor)

    used = requested = reason = None
    degraded = False
    provider = None
    if payload.mode in ("semantic", "hybrid"):
        resolved = resolve_embedding_provider(db=db)
        provider = resolved.provider
        used = resolved.provider.model_name
        requested = resolved.requested
        degraded = resolved.degraded
        reason = resolved.reason

    hits = hybrid_search(
        db, payload.q, visible_ids=visible, limit=limit, mode=payload.mode, provider=provider
    )
    return HybridSearchResponse(
        query=payload.q,
        mode=payload.mode,
        embedding_provider_used=used,
        embedding_provider_requested=requested,
        degraded=degraded,
        degraded_reason=reason,
        items=[
            HybridSearchItem(
                work_id=hit.work.id,
                title=hit.work.canonical_title,
                year=hit.work.year,
                score=round(hit.score, 6),
                passage=hit.passage,
                section=hit.section,
                lexical_rank=hit.lexical_rank,
                semantic_rank=hit.semantic_rank,
            )
            for hit in hits
        ],
    )


@router.post("/semantic", response_model=SemanticSearchResponse)
def search_semantic(
    payload: SemanticSearchRequest, db: Session = DB_DEP, actor: User = AUTH_DEP
) -> SemanticSearchResponse:
    """Rank works by similarity to a free-text query. Read-only: embeddings are built off this
    path (on import / via /search/reindex), so a search never writes to the database.

    Access control: results are filtered to papers the caller may SEE, applied *inside* the ranking
    (selectivity-adaptive: exact over a small visible set, else ANN with an allow-list) so the
    visible top-N is exact — no over-fetch heuristic."""
    limit = max(1, min(payload.limit, 50))
    visible = access.visible_work_ids(db, actor)

    # In embedding mode, resolve the provider up front so the response can report the one actually
    # used vs the one configured (a silent hash-BOW fallback surfaces here). Lexical mode uses no
    # embeddings, so these fields stay null.
    if payload.mode == "lexical":
        items = [
            SemanticSearchItem(
                work_id=hit.work.id,
                title=hit.work.canonical_title,
                year=hit.work.year,
                score=round(hit.score, 4),
            )
            for hit in lexical_search_papers(db, payload.q, visible_ids=visible, limit=limit)
        ]
        used = requested = reason = None
        degraded = False
    else:
        resolved = resolve_embedding_provider(db=db)
        items = [
            SemanticSearchItem(
                work_id=hit.work.id,
                title=hit.work.canonical_title,
                year=hit.work.year,
                score=round(hit.score, 4),
                passage=hit.passage,
                section=hit.section,
            )
            for hit in semantic_search_papers(
                db, payload.q, visible_ids=visible, limit=limit, provider=resolved.provider
            )
        ]
        used = resolved.provider.model_name
        requested = resolved.requested
        degraded = resolved.degraded
        reason = resolved.reason

    return SemanticSearchResponse(
        query=payload.q,
        mode=payload.mode,
        embedding_provider_used=used,
        embedding_provider_requested=requested,
        degraded=degraded,
        degraded_reason=reason,
        items=items,
    )


@router.post("/reindex")
def reindex_embeddings(
    db: Session = DB_DEP, _: User = EDITOR_DEP
) -> dict[str, int | str | bool | None]:
    """Embed any works missing a vector for the active provider (owner/editor). Returns count added.

    Embeddings are normally created on import in the background; this rebuilds them on demand (e.g.
    after a bulk import while the worker was down, or after switching embedding providers). Also
    backfills chunk-level embeddings for the active model (no-op unless a real model with a pgvector
    column is active on Postgres).

    Surfaces provider provenance so the UI can warn when the requested provider (e.g. an Ollama
    model) was unavailable and the reindex silently ran under the hash-BOW fallback instead."""
    resolved = resolve_embedding_provider(db=db)
    provider = resolved.provider
    added = ensure_work_embeddings(db, provider=provider)
    chunks_indexed = backfill_chunk_embeddings(db, provider=provider)
    db.commit()
    return {
        "indexed": added,
        "chunks_indexed": chunks_indexed,
        "status": "ok",
        "embedding_provider_used": provider.model_name,
        "embedding_provider_requested": resolved.requested,
        "degraded": resolved.degraded,
        "degraded_reason": resolved.reason,
    }


@router.post("/warm")
def warm_search(db: Session = DB_DEP, _: User = AUTH_DEP) -> dict[str, int | str]:
    """Warm the BM25F+ lexical index into memory (call when the library view opens) so the first
    lexical/hybrid search is hot. Idempotent — the index is rebuilt only when the corpus changed."""
    index = get_index(db)
    return {"lexical_indexed_docs": len(index.work_ids), "status": "ok"}
