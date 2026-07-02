"""Dynamic embedding-model registry + per-model pgvector-column provisioning (#21).

Each embedding model gets its own dimension-constrained ``vector`` column on ``work_chunks`` so a
real HNSW/ANN index can be built and vectors from different models are never compared. This module
lets an admin register *any* pulled model at runtime: it records the mapping in
``embedding_model_registry`` and (on Postgres) provisions the column + HNSW index by best-effort DDL,
bounded by a slug allowlist and a model cap. Models can be unregistered (column + index dropped) so
the cap is never a dead end.

SQLite / narrow unit-test schemas: when the registry table is absent we fall back to the static
``chunk_embeddings.CHUNK_MODEL_COLUMNS`` map, and provisioning is a no-op — the dialect-agnostic
document-level path still works.
"""

from __future__ import annotations

import logging
import re

from sqlalchemy import func, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.embedding_registry import EmbeddingModelRegistry
from app.services.embeddings import (
    EmbeddingProvider,
    HashBowProvider,
    cached_provider,
    evict_cached_providers,
)

logger = logging.getLogger(__name__)

# Upper bound on provisioned model columns (each is a physical column + HNSW index on work_chunks).
# Deletion frees a slot, so this is a guardrail against runaway growth, not a hard ceiling on choice.
MAX_EMBEDDING_MODELS = 8

# Column names are only ever interpolated into SQL after passing this pattern (defense-in-depth on
# top of slugify, which is the sole source of column names).
_SAFE_COLUMN = re.compile(r"^vec_[a-z0-9_]+$")

_TABLE_PRESENT: dict[int, bool] = {}


def _table_present(db: Session) -> bool:
    key = id(db.get_bind())
    if key not in _TABLE_PRESENT:
        _TABLE_PRESENT[key] = inspect(db.connection()).has_table(
            EmbeddingModelRegistry.__tablename__
        )
    return _TABLE_PRESENT[key]


def _is_postgres(db: Session) -> bool:
    return db.bind is not None and db.bind.dialect.name == "postgresql"


def slugify(model_name: str) -> str:
    """Derive a short, SQL-safe slug ([a-z0-9_]) from a model name; also the vec_ column suffix."""
    slug = re.sub(r"[^a-z0-9]+", "_", (model_name or "").lower()).strip("_")
    return slug[:56] or "model"


def column_for(db: Session, model_name: str) -> tuple[str, int] | None:
    """Return ``(column, dim)`` for a model from the registry, or None if it has no column.

    Falls back to the static map when the registry table is absent (SQLite unit tests)."""
    if not _table_present(db):
        from app.services.chunk_embeddings import CHUNK_MODEL_COLUMNS  # noqa: PLC0415

        return CHUNK_MODEL_COLUMNS.get(model_name)
    row = db.scalar(
        select(EmbeddingModelRegistry).where(EmbeddingModelRegistry.model_name == model_name)
    )
    if row is None:
        return None
    return (row.column_name, row.dim)


def active_models(db: Session) -> list[EmbeddingModelRegistry]:
    """Registered, active models that have a chunk-vector column (for the multimode selector)."""
    if not _table_present(db):
        return []
    return list(
        db.scalars(
            select(EmbeddingModelRegistry)
            .where(EmbeddingModelRegistry.active.is_(True))
            .order_by(EmbeddingModelRegistry.created_at)
        )
    )


def _parse_model_name(model_name: str) -> tuple[str, str]:
    """Split a resolved model_name into ``(provider, raw_model)`` for rebuilding a provider."""
    if model_name.startswith("ollama:"):
        return "ollama", model_name[len("ollama:") :]
    if model_name.startswith("st:"):
        return "sentence_transformers", model_name[len("st:") :]
    return "hash_bow", model_name


def provider_for(
    db: Session, model_name: str, *, ollama_url: str | None = None
) -> EmbeddingProvider:
    """Rebuild the embedding provider for a registered model (used by multimode search)."""
    row = None
    if _table_present(db):
        row = db.scalar(
            select(EmbeddingModelRegistry).where(EmbeddingModelRegistry.model_name == model_name)
        )
    provider, raw = (row.provider, row.raw_model) if row else _parse_model_name(model_name)
    if provider == "ollama":
        if ollama_url is None:
            from app.services.ai_config import get_ai_config  # noqa: PLC0415

            ollama_url = get_ai_config(db).ollama_url
        return cached_provider("ollama", raw, ollama_url)
    if provider == "sentence_transformers":
        return cached_provider("sentence_transformers", raw)
    return HashBowProvider()


def register(
    db: Session, *, model_name: str, provider: str, raw_model: str, dim: int
) -> tuple[str, int] | None:
    """Provision a model's chunk-vector column (runtime DDL on Postgres) + record it. Idempotent.

    Returns ``(column, dim)`` or None if not on Postgres / the registry table is absent. Raises
    ``ValueError`` when the model cap is reached (delete an unused model to free a slot)."""
    if not _table_present(db) or not _is_postgres(db):
        return None
    existing = db.scalar(
        select(EmbeddingModelRegistry).where(EmbeddingModelRegistry.model_name == model_name)
    )
    if existing is not None:
        return (existing.column_name, existing.dim)

    # Cap on TOTAL provisioned columns (inactive models keep their vec_* column too), so the cap
    # actually bounds physical columns on work_chunks (audit: stability #3).
    total_count = int(db.scalar(select(func.count()).select_from(EmbeddingModelRegistry)) or 0)
    if total_count >= MAX_EMBEDDING_MODELS:
        raise ValueError(
            f"Embedding-model cap reached ({MAX_EMBEDDING_MODELS}). Delete an unused model first."
        )

    slug = slugify(model_name)
    column = f"vec_{slug}"
    # Disambiguate a slug collision (different model, same slug) with a numeric suffix.
    n = 2
    used = {r.column_name for r in db.scalars(select(EmbeddingModelRegistry))}
    while column in used or db.get(EmbeddingModelRegistry, slug) is not None:
        slug = f"{slugify(model_name)[:52]}_{n}"
        column = f"vec_{slug}"
        n += 1
    if not _SAFE_COLUMN.match(column):  # unreachable given slugify; guards SQL interpolation
        raise ValueError(f"Unsafe column name derived from model: {column!r}")

    db.execute(
        text(f"ALTER TABLE work_chunks ADD COLUMN IF NOT EXISTS {column} vector({int(dim)})")
    )  # noqa: S608
    # SAVEPOINT so a failed CREATE INDEX doesn't abort the surrounding transaction (Postgres would
    # otherwise refuse every later statement with InFailedSqlTransaction).
    try:
        with db.begin_nested():
            db.execute(
                text(
                    f"CREATE INDEX IF NOT EXISTS ix_work_chunks_{column} "  # noqa: S608
                    f"ON work_chunks USING hnsw ({column} vector_cosine_ops)"
                )
            )
    except Exception as exc:  # noqa: BLE001 - older pgvector without HNSW: exact scan still works
        logger.warning("Could not build HNSW index on work_chunks.%s (%s).", column, exc)

    # Insert the registry row in a SAVEPOINT so a concurrent reindex that registered the same model
    # first (unique model_name) loses the race gracefully: we roll back just the insert and reuse
    # the winner's row instead of failing the whole job (audit: stability #1). The DDL above is
    # IF NOT EXISTS, so it's already idempotent across the racing jobs.
    try:
        with db.begin_nested():
            db.add(
                EmbeddingModelRegistry(
                    slug=slug,
                    model_name=model_name,
                    provider=provider,
                    raw_model=raw_model,
                    dim=int(dim),
                    column_name=column,
                    active=True,
                )
            )
    except IntegrityError:
        winner = db.scalar(
            select(EmbeddingModelRegistry).where(EmbeddingModelRegistry.model_name == model_name)
        )
        if winner is not None:
            return (winner.column_name, winner.dim)
        raise
    return (column, dim)


def register_provider(db: Session, provider: EmbeddingProvider) -> tuple[str, int] | None:
    """Register a live provider by probing its output dimension (used by auto-provision on index)."""
    kind, raw = _parse_model_name(provider.model_name)
    if kind == "hash_bow":  # the SQLite-testable baseline stays column-less by design
        return None
    dim = len(provider.embed("probe"))
    if dim <= 0:
        return None
    return register(db, model_name=provider.model_name, provider=kind, raw_model=raw, dim=dim)


def unregister_by_model_name(db: Session, model_name: str) -> bool:
    """Unregister by resolved model_name (drops column + index + row). Used when deleting weights."""
    if not _table_present(db):
        return False
    row = db.scalar(
        select(EmbeddingModelRegistry).where(EmbeddingModelRegistry.model_name == model_name)
    )
    return unregister(db, row.slug) if row is not None else False


def unregister(db: Session, slug: str) -> bool:
    """Drop a model's column + HNSW index and remove its registry row. Frees a cap slot."""
    if not _table_present(db):
        return False
    row = db.get(EmbeddingModelRegistry, slug)
    if row is None:
        return False
    if _is_postgres(db) and _SAFE_COLUMN.match(row.column_name):
        # Bound the DDL wait: DROP COLUMN takes an ACCESS EXCLUSIVE lock on work_chunks, which
        # would otherwise queue behind a long reindex and block every reader. Let the timeout
        # propagate so the API layer can turn it into a 409.
        db.execute(text("SET LOCAL lock_timeout = '5s'"))
        db.execute(text(f"DROP INDEX IF EXISTS ix_work_chunks_{row.column_name}"))  # noqa: S608
        db.execute(text(f"ALTER TABLE work_chunks DROP COLUMN IF EXISTS {row.column_name}"))  # noqa: S608
    model_name = row.model_name
    db.delete(row)
    db.flush()
    evict_cached_providers(model_name)
    return True
