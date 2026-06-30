"""Semantic (vector) and lexical search over works (SPEC §8.15, Stage 6).

Embeddings are built **off the read path**: on import / via a background RQ job / via an explicit
reindex, never inside a search request. ``semantic_search`` only reads stored vectors and embeds
the query in memory, so a normal search performs no database writes. A ``lexical`` mode ranks by
term overlap and needs no embeddings at all — the UI offers both modes.
"""

from __future__ import annotations

import contextlib
import re
import uuid
from dataclasses import dataclass

from sqlalchemy import delete, func, or_, select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.ai import Embedding
from app.models.work import Work
from app.services.embeddings import (
    EmbeddingProvider,
    HashBowProvider,
    cosine_similarity,
    get_embedding_provider,
)

_WORD = re.compile(r"[A-Za-z][A-Za-z0-9'-]+")


@dataclass
class SearchHit:
    work: Work
    score: float


# --- optional pgvector acceleration (H7) ------------------------------------


def _pgvector_on(db: Session) -> bool:
    """Whether to use the pgvector ranking path (Postgres + enabled setting)."""
    from app.core.config import get_settings  # noqa: PLC0415

    return (
        get_settings().pgvector_enabled
        and db.bind is not None
        and db.bind.dialect.name == "postgresql"
    )


def _vec_literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{x:.8g}" for x in vector) + "]"


def _write_pgvector(db: Session, embedding_id, vector: list[float]) -> None:
    """Mirror a stored vector into the pgvector column (best-effort; ignored if unavailable)."""
    if not _pgvector_on(db):
        return
    with contextlib.suppress(SQLAlchemyError):
        db.execute(
            text("UPDATE embeddings SET vector_pg = CAST(:v AS vector) WHERE id = :id"),
            {"v": _vec_literal(vector), "id": str(embedding_id)},
        )


def _pgvector_rank(
    db: Session, query_vector: list[float], *, model_name: str, limit: int
) -> list[tuple] | None:
    """Rank by pgvector cosine distance in the database. Returns (id, score) or None on failure."""
    try:
        rows = db.execute(
            text(
                "SELECT entity_id, 1 - (vector_pg <=> CAST(:q AS vector)) AS score "
                "FROM embeddings "
                "WHERE entity_type = 'work' AND model_name = :m AND vector_pg IS NOT NULL "
                "ORDER BY vector_pg <=> CAST(:q AS vector) LIMIT :n"
            ),
            {"q": _vec_literal(query_vector), "m": model_name, "n": limit},
        ).all()
        return [(r[0], float(r[1])) for r in rows]
    except SQLAlchemyError:
        return None


def _work_text(work: Work) -> str:
    return " ".join(part for part in (work.canonical_title, work.abstract) if part).strip()


def ensure_work_embeddings(db: Session, *, provider: EmbeddingProvider | None = None) -> int:
    """Embed and store any works missing an embedding for the provider's model. Returns count added.

    Used by the background index job and the reindex endpoint — **not** by a search request. Each
    insert is guarded so a concurrent indexer racing on the unique ``(entity, model)`` key is a
    no-op rather than an error.
    """
    provider = provider or HashBowProvider()
    model_name = provider.model_name
    indexed = set(
        db.scalars(
            select(Embedding.entity_id).where(
                Embedding.entity_type == "work", Embedding.model_name == model_name
            )
        ).all()
    )
    added = 0
    for work in db.scalars(select(Work)).all():
        if work.id in indexed:
            continue
        doc = _work_text(work)
        if not doc:
            continue
        if _store_embedding(db, work_id=work.id, model_name=model_name, vector=provider.embed(doc)):
            added += 1
    if added:
        db.flush()
    return added


def index_one_work(db: Session, work: Work, *, provider: EmbeddingProvider | None = None) -> bool:
    """(Re)embed a single work (per-import background job). Replaces any prior vector for the model
    so an updated title/abstract re-embeds. Returns True if a vector was stored."""
    provider = provider or HashBowProvider()
    doc = _work_text(work)
    if not doc:
        return False
    vector = provider.embed(doc)
    db.execute(
        delete(Embedding).where(
            Embedding.entity_type == "work",
            Embedding.entity_id == work.id,
            Embedding.model_name == provider.model_name,
        )
    )
    row = Embedding(
        entity_type="work",
        entity_id=work.id,
        model_name=provider.model_name,
        dim=len(vector),
        vector=vector,
    )
    db.add(row)
    db.flush()
    _write_pgvector(db, row.id, vector)
    return True


def _store_embedding(
    db: Session, *, work_id: uuid.UUID, model_name: str, vector: list[float]
) -> bool:
    """Insert an embedding, treating a unique-key clash (already indexed) as success-no-op."""
    existing = db.scalar(
        select(Embedding.id).where(
            Embedding.entity_type == "work",
            Embedding.entity_id == work_id,
            Embedding.model_name == model_name,
        )
    )
    if existing is not None:
        return False
    savepoint = db.begin_nested()
    try:
        row = Embedding(
            entity_type="work",
            entity_id=work_id,
            model_name=model_name,
            dim=len(vector),
            vector=vector,
        )
        db.add(row)
        savepoint.commit()
    except IntegrityError:
        savepoint.rollback()  # another indexer won the race; that's fine
        return False
    _write_pgvector(db, row.id, vector)
    return True


def reindex_status(db: Session, *, provider: EmbeddingProvider | None = None) -> dict:
    """Report embedding-index coverage for the active model: ``{model_name, indexed, total}``."""
    provider = provider or HashBowProvider()
    # Two SQL aggregates (E5) — count works that have indexable text, and those embedded for the
    # active model — rather than materializing every Work row in Python.
    total = db.scalar(
        select(func.count())
        .select_from(Work)
        .where(
            or_(
                func.coalesce(Work.canonical_title, "") != "",
                func.coalesce(Work.abstract, "") != "",
            )
        )
    )
    indexed = db.scalar(
        select(func.count())
        .select_from(Embedding)
        .where(Embedding.entity_type == "work", Embedding.model_name == provider.model_name)
    )
    return {
        "model_name": provider.model_name,
        "indexed": int(indexed or 0),
        "total": int(total or 0),
    }


def related_works(
    db: Session, work: Work, *, limit: int = 10, provider: EmbeddingProvider | None = None
) -> list[SearchHit]:
    """Find works most similar to ``work`` (SPEC §8.17.2). Read-only.

    Uses stored embeddings for the active model (cosine); if the target work has no embedding,
    falls back to the work's own title+abstract text as the query.
    """
    provider = provider or get_embedding_provider(db=db)
    target = db.scalar(
        select(Embedding).where(
            Embedding.entity_type == "work",
            Embedding.entity_id == work.id,
            Embedding.model_name == provider.model_name,
        )
    )
    query_vector = target.vector if target else provider.embed(_work_text(work))

    # Fast path: rank in Postgres via pgvector when enabled (E4); else Python cosine.
    top: list[tuple] | None = None
    if _pgvector_on(db):
        ranked = _pgvector_rank(db, query_vector, model_name=provider.model_name, limit=limit + 1)
        if ranked is not None:
            top = [(wid, score) for wid, score in ranked if wid != work.id][:limit]

    if top is None:
        rows = db.scalars(
            select(Embedding).where(
                Embedding.entity_type == "work", Embedding.model_name == provider.model_name
            )
        ).all()
        scored: list[tuple[uuid.UUID, float]] = []
        for emb in rows:
            if emb.entity_id == work.id:
                continue
            score = cosine_similarity(query_vector, emb.vector)
            if score > 0.0:
                scored.append((emb.entity_id, score))
        scored.sort(key=lambda i: i[1], reverse=True)
        top = scored[:limit]
    works = {
        w.id: w for w in db.scalars(select(Work).where(Work.id.in_([wid for wid, _ in top]))).all()
    }
    return [SearchHit(work=works[wid], score=score) for wid, score in top if wid in works]


def _lexical_search(db: Session, query: str, *, limit: int) -> list[SearchHit]:
    """Rank works by query-term overlap with title + abstract (no embeddings required)."""
    terms = {t for t in _WORD.findall(query.lower()) if len(t) > 1}
    if not terms:
        return []
    hits: list[SearchHit] = []
    for work in db.scalars(select(Work)).all():
        tokens = _WORD.findall(_work_text(work).lower())
        if not tokens:
            continue
        token_set = set(tokens)
        overlap = sum(1 for t in terms if t in token_set)
        if overlap:
            # Coverage of the query, lightly rewarding repeated matches.
            density = sum(tokens.count(t) for t in terms) / len(tokens)
            hits.append(SearchHit(work=work, score=round(overlap / len(terms) + density, 4)))
    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:limit]


def semantic_search(
    db: Session,
    query: str,
    *,
    limit: int = 10,
    mode: str = "embedding",
    provider: EmbeddingProvider | None = None,
    auto_index: bool = False,
) -> list[SearchHit]:
    """Return works ranked by the query (most relevant first).

    ``mode='embedding'`` (default) ranks by cosine similarity over stored vectors and is
    **read-only** unless ``auto_index`` is set (tests / explicit reindex). ``mode='lexical'``
    ranks by term overlap and never touches embeddings.
    """
    if not (query or "").strip():
        return []
    if mode == "lexical":
        return _lexical_search(db, query, limit=limit)

    provider = provider or get_embedding_provider(db=db)
    if auto_index:
        ensure_work_embeddings(db, provider=provider)

    query_vector = provider.embed(query)

    # Fast path: rank in Postgres via pgvector when enabled (falls through to Python on any miss).
    top: list[tuple] | None = None
    if _pgvector_on(db):
        top = _pgvector_rank(db, query_vector, model_name=provider.model_name, limit=limit)

    if top is None:
        rows = db.scalars(
            select(Embedding).where(
                Embedding.entity_type == "work", Embedding.model_name == provider.model_name
            )
        ).all()
        scored: list[tuple[uuid.UUID, float]] = []
        for embedding in rows:
            score = cosine_similarity(query_vector, embedding.vector)
            if score > 0.0:
                scored.append((embedding.entity_id, score))
        scored.sort(key=lambda item: item[1], reverse=True)
        top = scored[:limit]

    works = {
        work.id: work
        for work in db.scalars(select(Work).where(Work.id.in_([wid for wid, _ in top]))).all()
    }
    return [SearchHit(work=works[wid], score=score) for wid, score in top if wid in works]
