"""Embedding-model registry: dynamic model ↔ chunk-vector-column mapping (#21).

Creates the dialect-agnostic ``embedding_model_registry`` table and seeds the two models that
migration 0035 already provisioned columns for (all-MiniLM-L6-v2 → vec_minilm, nomic-embed-text →
vec_nomic). New models are added at runtime by ``services.embedding_registry`` (row + best-effort
ALTER TABLE ADD COLUMN + HNSW index on Postgres).

Revision ID: 0036_embedding_model_registry
Revises: 0035_work_chunk_vectors
Create Date: 2026-07-02
"""

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "0036_embedding_model_registry"
down_revision: str | None = "0035_work_chunk_vectors"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (slug, model_name, provider, raw_model, dim, column_name) for the models 0035 pre-provisioned.
_SEED = (
    (
        "minilm",
        "st:sentence-transformers/all-MiniLM-L6-v2",
        "sentence_transformers",
        "sentence-transformers/all-MiniLM-L6-v2",
        384,
        "vec_minilm",
    ),
    (
        "nomic",
        "ollama:nomic-embed-text:latest",
        "ollama",
        "nomic-embed-text:latest",
        768,
        "vec_nomic",
    ),
)


def upgrade() -> None:
    table = op.create_table(
        "embedding_model_registry",
        sa.Column("slug", sa.String(length=64), primary_key=True),
        sa.Column("model_name", sa.String(length=255), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("raw_model", sa.String(length=255), nullable=False),
        sa.Column("dim", sa.Integer(), nullable=False),
        sa.Column("column_name", sa.String(length=64), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_embedding_model_registry_model_name",
        "embedding_model_registry",
        ["model_name"],
        unique=True,
    )
    now = datetime.now(UTC)
    op.bulk_insert(
        table,
        [
            {
                "slug": slug,
                "model_name": model_name,
                "provider": provider,
                "raw_model": raw_model,
                "dim": dim,
                "column_name": column_name,
                "active": True,
                "created_at": now,
            }
            for slug, model_name, provider, raw_model, dim, column_name in _SEED
        ],
    )


def downgrade() -> None:
    op.drop_index("ix_embedding_model_registry_model_name", table_name="embedding_model_registry")
    op.drop_table("embedding_model_registry")
