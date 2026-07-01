"""Hybrid search: fuse BM25F+ lexical and dense semantic rankings with RRF (HYBRID-SEARCH-DESIGN §4).

Reciprocal Rank Fusion combines the two paper-level rankings without needing to normalize their
very different score scales (unbounded BM25 vs [0,1] cosine):

    rrf(paper) = Σ_engine  1 / (k + rank_engine(paper)),   k = 60

Three modes: ``lexical`` (BM25F+ only), ``semantic`` (dense only), ``hybrid`` (fuse both). In hybrid
mode the matching passage/section is carried over from the semantic side. All ranking is filtered to
the caller's visible works upstream (the two engines each apply ``visible_ids``).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models.work import Work
from app.services.bm25_index import lexical_search_papers
from app.services.chunk_search import PaperHit, semantic_search_papers
from app.services.embeddings import EmbeddingProvider

RRF_K = 60
# How deep to pull from each engine before fusing (RRF benefits from depth beyond the page size).
RRF_DEPTH = 60


@dataclass
class HybridHit:
    work: Work
    score: float
    passage: str | None = None
    section: str | None = None
    lexical_rank: int | None = None
    semantic_rank: int | None = None


def _fuse(lexical: list[PaperHit], semantic: list[PaperHit], *, limit: int) -> list[HybridHit]:
    """Reciprocal Rank Fusion of two paper rankings (best passage taken from the semantic side)."""
    lexical_rank = {hit.work.id: i + 1 for i, hit in enumerate(lexical)}
    semantic_rank = {hit.work.id: i + 1 for i, hit in enumerate(semantic)}
    works: dict = {}
    passages: dict = {}
    for hit in lexical:
        works.setdefault(hit.work.id, hit.work)
    for hit in semantic:
        works.setdefault(hit.work.id, hit.work)
        passages[hit.work.id] = (hit.passage, hit.section)

    fused: list[HybridHit] = []
    for work_id, work in works.items():
        score = 0.0
        lr = lexical_rank.get(work_id)
        sr = semantic_rank.get(work_id)
        if lr is not None:
            score += 1.0 / (RRF_K + lr)
        if sr is not None:
            score += 1.0 / (RRF_K + sr)
        passage, section = passages.get(work_id, (None, None))
        fused.append(
            HybridHit(
                work=work,
                score=score,
                passage=passage,
                section=section,
                lexical_rank=lr,
                semantic_rank=sr,
            )
        )
    fused.sort(key=lambda h: h.score, reverse=True)
    return fused[:limit]


def hybrid_search(
    db: Session,
    query: str,
    *,
    visible_ids: set | None,
    limit: int = 10,
    mode: str = "hybrid",
    provider: EmbeddingProvider | None = None,
) -> list[HybridHit]:
    """Rank papers for ``query`` in the requested mode, filtered to visible works. Read-only."""
    if not (query or "").strip():
        return []

    if mode == "lexical":
        return [
            HybridHit(work=h.work, score=h.score, lexical_rank=i + 1)
            for i, h in enumerate(
                lexical_search_papers(db, query, visible_ids=visible_ids, limit=limit)
            )
        ]
    if mode == "semantic":
        return [
            HybridHit(
                work=h.work,
                score=h.score,
                passage=h.passage,
                section=h.section,
                semantic_rank=i + 1,
            )
            for i, h in enumerate(
                semantic_search_papers(
                    db, query, visible_ids=visible_ids, limit=limit, provider=provider
                )
            )
        ]
    # hybrid: pull depth from each engine, then RRF-fuse.
    depth = max(limit, RRF_DEPTH)
    lexical = lexical_search_papers(db, query, visible_ids=visible_ids, limit=depth)
    semantic = semantic_search_papers(
        db, query, visible_ids=visible_ids, limit=depth, provider=provider
    )
    return _fuse(lexical, semantic, limit=limit)
