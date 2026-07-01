"""Chunk-level per-model embeddings + ANN plumbing (HYBRID-SEARCH-DESIGN §3.2).

Postgres-only: each supported model has its own dimension-constrained pgvector column on
``work_chunks`` with an HNSW index (migration 0035). A column is bound to **exactly one** model —
vectors from different models are never comparable — so an unsupported/other model simply gets no
column and semantic search degrades to the document-level baseline (``services.semantic_search``),
which stays the dialect-agnostic, SQLite-testable path.

Storing per-chunk vectors is idempotent: re-embedding overwrites the work's chunk columns.
"""

from __future__ import annotations

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.models.chunk import WorkChunk
from app.models.work import Work
from app.services.embeddings import EmbeddingProvider, get_embedding_provider

# model_name -> (pgvector column, dimension). Keep in sync with migration 0035's _COLUMNS.
# Keyed by the *resolved* provider model_name (e.g. "st:..."/"ollama:...").
CHUNK_MODEL_COLUMNS: dict[str, tuple[str, int]] = {
    "st:sentence-transformers/all-MiniLM-L6-v2": ("vec_minilm", 384),
    # Canonicalized to the tagged form (see embeddings.normalize_ollama_model): the provider now
    # reports "ollama:nomic-embed-text:latest" so its vectors keep landing in vec_nomic.
    "ollama:nomic-embed-text:latest": ("vec_nomic", 768),
}

# Whitelist of column names we will ever interpolate into SQL (defense-in-depth: the only source is
# the constant above, but this makes the safety explicit).
_ALLOWED_COLUMNS = {column for column, _dim in CHUNK_MODEL_COLUMNS.values()}


def chunk_column_for(model_name: str) -> tuple[str, int] | None:
    """Return ``(column, dim)`` for a model, or None if it has no dedicated chunk column."""
    return CHUNK_MODEL_COLUMNS.get(model_name)


def _is_postgres(db: Session) -> bool:
    return db.bind is not None and db.bind.dialect.name == "postgresql"


def _vec_literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{x:.8g}" for x in vector) + "]"


def _resolve_column(provider: EmbeddingProvider) -> str | None:
    col = chunk_column_for(provider.model_name)
    if col is None:
        return None
    column, _dim = col
    if column not in _ALLOWED_COLUMNS:  # unreachable given the registry; guards SQL interpolation
        return None
    return column


def embed_work_chunks(db: Session, work: Work, *, provider: EmbeddingProvider | None = None) -> int:
    """Embed a work's chunks under the active model into its pgvector column. Returns count written.

    No-op (returns 0) unless we're on Postgres and the active model has a chunk column. Overwrites
    the column for the work's chunks, so a re-chunk + re-embed is idempotent.
    """
    provider = provider or get_embedding_provider(db=db)
    column = _resolve_column(provider)
    if column is None or not _is_postgres(db):
        return 0
    chunks = list(db.scalars(select(WorkChunk).where(WorkChunk.work_id == work.id)))
    written = 0
    for chunk in chunks:
        db.execute(
            text(f"UPDATE work_chunks SET {column} = CAST(:v AS vector) WHERE id = :id"),  # noqa: S608
            {"v": _vec_literal(provider.embed(chunk.text)), "id": str(chunk.id)},
        )
        written += 1
    return written


def backfill_chunk_embeddings(db: Session, *, provider: EmbeddingProvider | None = None) -> int:
    """Fill the active model's chunk column for every chunk still missing it. Returns count written.

    Used by the reindex job / backfill-on-activation: enabling a real model embeds the whole corpus
    once, after which switching back to it is instant (the column is retained).
    """
    provider = provider or get_embedding_provider(db=db)
    column = _resolve_column(provider)
    if column is None or not _is_postgres(db):
        return 0
    rows = db.execute(
        text(f"SELECT id, text FROM work_chunks WHERE {column} IS NULL")  # noqa: S608
    ).all()
    written = 0
    for chunk_id, chunk_text in rows:
        db.execute(
            text(f"UPDATE work_chunks SET {column} = CAST(:v AS vector) WHERE id = :id"),  # noqa: S608
            {"v": _vec_literal(provider.embed(chunk_text or "")), "id": str(chunk_id)},
        )
        written += 1
    return written


def chunk_embedding_status(db: Session, *, provider: EmbeddingProvider | None = None) -> dict:
    """Report chunk-embedding coverage for the active model: {model_name, column, indexed, total}.

    ``column`` is None (and ``indexed`` 0) when the active model has no chunk column or we're not on
    Postgres — i.e. semantic search is served by the document-level baseline, not chunk ANN.
    """
    provider = provider or get_embedding_provider(db=db)
    col = chunk_column_for(provider.model_name)
    total = int(db.scalar(select(func.count()).select_from(WorkChunk)) or 0)
    if col is None or not _is_postgres(db):
        return {"model_name": provider.model_name, "column": None, "indexed": 0, "total": total}
    column, _dim = col
    indexed = int(
        db.execute(
            text(f"SELECT count(*) FROM work_chunks WHERE {column} IS NOT NULL")  # noqa: S608
        ).scalar()
        or 0
    )
    return {"model_name": provider.model_name, "column": column, "indexed": indexed, "total": total}
