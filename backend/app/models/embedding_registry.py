"""Registry of embedding models that have a dedicated chunk-vector column (#21).

The hybrid-search design binds each embedding model to its own dimension-constrained pgvector
column on ``work_chunks`` (so a real HNSW/ANN index can be built and vectors from different models
are never compared). The original design hardcoded that mapping in code + a fixed migration; this
table makes it **dynamic**: an admin can pull any embedding model and, on first index, its column is
provisioned by runtime DDL and recorded here. Users can then search/cluster with any registered
model, or "multimode" (RRF across all of them).

The table itself is dialect-agnostic (so the SQLite unit-test path can read it); the ``vector``
columns it points at are Postgres-only and provisioned by ``services.embedding_registry``.
"""

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class EmbeddingModelRegistry(Base):
    """One embedding model ↔ its ``work_chunks`` pgvector column."""

    __tablename__ = "embedding_model_registry"

    # Short, SQL-safe identifier ([a-z0-9_]) derived from the model name; also the column suffix.
    slug: Mapped[str] = mapped_column(String(64), primary_key=True)
    # Resolved provider key (e.g. "ollama:nomic-embed-text:latest") — the stored embedding namespace.
    model_name: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    # Provider kind: "hash_bow" | "sentence_transformers" | "ollama".
    provider: Mapped[str] = mapped_column(String(32))
    # The provider-native model name (e.g. "nomic-embed-text:latest") used to rebuild the provider.
    raw_model: Mapped[str] = mapped_column(String(255))
    dim: Mapped[int] = mapped_column(Integer)
    # Physical pgvector column on work_chunks (e.g. "vec_nomic"); "vec_" + slug for new models.
    column_name: Mapped[str] = mapped_column(String(64))
    # Inactive models keep their column/vectors but are excluded from multimode + selection lists.
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
