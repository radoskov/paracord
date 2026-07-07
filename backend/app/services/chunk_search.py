"""Chunk-level semantic search with selectivity-adaptive access filtering (HYBRID-SEARCH-DESIGN §3.3).

Ranks passages under the active model's pgvector column, then rolls the best passage per paper up to
a paper-level ranking. Access control is applied *inside* the query (no over-fetch heuristic):

- **low selectivity** (the caller may see only a small fraction of the library) → pre-filter + exact
  (index scan disabled) over just the visible chunks: exact, no post-filter recall cliff, and fast
  because the filtered set is small (arXiv:2602.11443 §5.1.1);
- **high selectivity** → HNSW ANN with the allow-list pushed down and pgvector's iterative index
  scan, which keeps traversing until enough rows pass the filter (avoids under-filling k).

When the active model has no chunk column (hash-BOW default, or not on Postgres) this degrades to the
document-level baseline (``services.semantic_search``), which stays the SQLite-testable path.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.models.work import Work
from app.services.chunk_embeddings import _vec_literal, chunk_column_for
from app.services.embeddings import EmbeddingProvider, get_embedding_provider
from app.services.semantic_search import semantic_search

# Below this visible-fraction, pre-filter + exact beats ANN post-filtering (the recall cliff).
SELECTIVITY_THRESHOLD = 0.10
# Fetch this many chunks per requested paper before rolling up (papers collapse many chunks).
CHUNK_FANOUT = 10
MAX_CHUNK_FETCH = 500


@dataclass
class PaperHit:
    """A paper-level semantic hit, with the best-matching passage that produced its score."""

    work: Work
    score: float
    passage: str | None = None
    section: str | None = None


def _is_postgres(db: Session) -> bool:
    return db.bind is not None and db.bind.dialect.name == "postgresql"


def _is_low_selectivity(db: Session, n_visible: int) -> bool:
    total = int(db.scalar(select(func.count()).select_from(Work)) or 0)
    return total > 0 and (n_visible / total) < SELECTIVITY_THRESHOLD


def _fetch_chunk_rows(
    db: Session,
    column: str,
    query_vector: list[float],
    *,
    visible_ids: set[uuid.UUID] | None,
    k: int,
) -> list[tuple]:
    """Return up to k ``(work_id, section, text, score)`` chunk rows, ranked by cosine similarity."""
    params: dict = {"q": _vec_literal(query_vector), "k": k}
    where = f"{column} IS NOT NULL"
    if visible_ids is not None:
        if not visible_ids:
            return []
        where += " AND work_id = ANY(:visible)"
        params["visible"] = [str(x) for x in visible_ids]

    # Strategy by selectivity (arXiv:2602.11443): exact over a small visible set vs ANN otherwise.
    if visible_ids is not None and _is_low_selectivity(db, len(visible_ids)):
        db.execute(text("SET LOCAL enable_indexscan = off"))
        db.execute(text("SET LOCAL enable_bitmapscan = off"))
    else:
        # pgvector >= 0.8 iterative scan: keep traversing HNSW until enough rows pass the filter.
        db.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))

    sql = (
        f"SELECT work_id, section, text, 1 - ({column} <=> CAST(:q AS vector)) AS score "  # noqa: S608
        f"FROM work_chunks WHERE {where} "
        f"ORDER BY {column} <=> CAST(:q AS vector) LIMIT :k"
    )
    return db.execute(text(sql), params).all()


def _rollup(db: Session, rows: list[tuple], limit: int) -> list[PaperHit]:
    """Collapse chunk rows to one hit per paper (best chunk wins), top ``limit`` papers."""
    best: dict[uuid.UUID, tuple[float, str | None, str | None]] = {}
    for work_id, section, chunk_text, score in rows:
        wid = uuid.UUID(str(work_id))
        score = float(score)
        if wid not in best or score > best[wid][0]:
            best[wid] = (score, section, chunk_text)
    ranked = sorted(best.items(), key=lambda kv: kv[1][0], reverse=True)[:limit]
    works = {
        w.id: w
        for w in db.scalars(
            select(Work).where(
                Work.id.in_([wid for wid, _ in ranked]), Work.merged_into_id.is_(None)
            )
        ).all()
    }
    hits: list[PaperHit] = []
    for wid, (score, section, passage) in ranked:
        work = works.get(wid)
        if work is not None:
            hits.append(PaperHit(work=work, score=score, passage=passage, section=section))
    return hits


def _fallback_doc_level(
    db: Session,
    query: str,
    *,
    visible_ids: set[uuid.UUID] | None,
    limit: int,
    provider: EmbeddingProvider,
) -> list[PaperHit]:
    hits = semantic_search(db, query, limit=limit, provider=provider, visible_ids=visible_ids)
    return [PaperHit(work=h.work, score=h.score) for h in hits]


def semantic_search_papers(
    db: Session,
    query: str,
    *,
    visible_ids: set[uuid.UUID] | None,
    limit: int = 10,
    provider: EmbeddingProvider | None = None,
) -> list[PaperHit]:
    """Rank papers for ``query`` by chunk-level semantic similarity, filtered to visible papers.

    ``visible_ids=None`` means unrestricted (admin/owner). Uses chunk-level ANN when the active
    model has a pgvector column on Postgres; otherwise the document-level baseline. Read-only.
    """
    if not (query or "").strip():
        return []
    provider = provider or get_embedding_provider(db=db)
    col = chunk_column_for(provider.model_name, db)
    if col is None or not _is_postgres(db):
        return _fallback_doc_level(
            db, query, visible_ids=visible_ids, limit=limit, provider=provider
        )
    column = col[0]
    k = min(max(1, limit) * CHUNK_FANOUT, MAX_CHUNK_FETCH)
    rows = _fetch_chunk_rows(db, column, provider.embed(query), visible_ids=visible_ids, k=k)
    return _rollup(db, rows, limit)
