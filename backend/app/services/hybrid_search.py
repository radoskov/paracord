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


MULTIMODE = "multimode"


def _rrf_paper_hits(rankings: list[list[PaperHit]], *, limit: int) -> list[PaperHit]:
    """RRF-fuse several paper rankings into one (used for multimode: one ranking per model).

    Rank-based fusion sidesteps the fact that different embedding models produce
    non-comparable cosine scales; the best passage is carried from whichever ranking ranked the
    paper highest."""
    rank_maps = [{h.work.id: i + 1 for i, h in enumerate(r)} for r in rankings]
    works: dict = {}
    best_passage: dict = {}  # work_id -> (rank, passage, section) from its best-ranking model
    for r in rankings:
        for i, h in enumerate(r):
            works.setdefault(h.work.id, h.work)
            prev = best_passage.get(h.work.id)
            if h.passage is not None and (prev is None or (i + 1) < prev[0]):
                best_passage[h.work.id] = (i + 1, h.passage, h.section)
    fused: list[PaperHit] = []
    for work_id, work in works.items():
        score = sum(1.0 / (RRF_K + rm[work_id]) for rm in rank_maps if work_id in rm)
        _, passage, section = best_passage.get(work_id, (0, None, None))
        fused.append(PaperHit(work=work, score=score, passage=passage, section=section))
    fused.sort(key=lambda h: h.score, reverse=True)
    return fused[:limit]


def _multimode_semantic(
    db: Session, query: str, *, visible_ids: set | None, limit: int
) -> list[PaperHit]:
    """Semantic ranking fused via RRF across every active registered embedding model (#21)."""
    from app.services.embedding_registry import active_models, provider_for  # noqa: PLC0415

    depth = max(limit, RRF_DEPTH)
    rankings: list[list[PaperHit]] = []
    for model in active_models(db):
        try:
            provider = provider_for(db, model.model_name)
            rankings.append(
                semantic_search_papers(
                    db, query, visible_ids=visible_ids, limit=depth, provider=provider
                )
            )
        except Exception:  # noqa: BLE001 - a broken/uninstalled model must not sink the others
            continue
    if not rankings:
        return []
    return _rrf_paper_hits(rankings, limit=limit)


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


def _semantic_ranking(
    db: Session,
    query: str,
    *,
    visible_ids: set | None,
    limit: int,
    provider: EmbeddingProvider | None,
    embedding_model: str | None,
) -> list[PaperHit]:
    """Semantic paper ranking under a single model, a specific registered model, or multimode."""
    if embedding_model == MULTIMODE:
        return _multimode_semantic(db, query, visible_ids=visible_ids, limit=limit)
    if provider is None and embedding_model:
        from app.services.embedding_registry import provider_for  # noqa: PLC0415

        provider = provider_for(db, embedding_model)
    return semantic_search_papers(
        db, query, visible_ids=visible_ids, limit=limit, provider=provider
    )


def hybrid_search(
    db: Session,
    query: str,
    *,
    visible_ids: set | None,
    limit: int = 10,
    mode: str = "hybrid",
    provider: EmbeddingProvider | None = None,
    embedding_model: str | None = None,
) -> list[HybridHit]:
    """Rank papers for ``query`` in the requested mode, filtered to visible works. Read-only.

    ``embedding_model`` selects which embeddings feed the semantic side: None → the configured
    active model; a registered ``model_name`` → that model; ``"multimode"`` → RRF across all active
    models (#21)."""
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
                _semantic_ranking(
                    db,
                    query,
                    visible_ids=visible_ids,
                    limit=limit,
                    provider=provider,
                    embedding_model=embedding_model,
                )
            )
        ]
    # hybrid: pull depth from each engine, then RRF-fuse.
    depth = max(limit, RRF_DEPTH)
    lexical = lexical_search_papers(db, query, visible_ids=visible_ids, limit=depth)
    semantic = _semantic_ranking(
        db,
        query,
        visible_ids=visible_ids,
        limit=depth,
        provider=provider,
        embedding_model=embedding_model,
    )
    return _fuse(lexical, semantic, limit=limit)
