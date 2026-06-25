"""create embeddings table

Revision ID: 0008_embeddings
Revises: 6a310e33c3d6
Create Date: 2026-06-25 00:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008_embeddings"
down_revision: str | None = "6a310e33c3d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    op.create_table(
        "embeddings",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("entity_type", sa.String(length=64), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("model_name", sa.String(length=255), nullable=False),
        sa.Column("dim", sa.Integer(), nullable=False),
        sa.Column("vector", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "entity_type", "entity_id", "model_name", name="uq_embedding_entity_model"
        ),
    )
    op.create_index(op.f("ix_embeddings_entity_id"), "embeddings", ["entity_id"], unique=False)
    op.create_index(op.f("ix_embeddings_entity_type"), "embeddings", ["entity_type"], unique=False)
    op.create_index(op.f("ix_embeddings_model_name"), "embeddings", ["model_name"], unique=False)


def downgrade() -> None:
    """Revert the migration."""
    op.drop_index(op.f("ix_embeddings_model_name"), table_name="embeddings")
    op.drop_index(op.f("ix_embeddings_entity_type"), table_name="embeddings")
    op.drop_index(op.f("ix_embeddings_entity_id"), table_name="embeddings")
    op.drop_table("embeddings")
