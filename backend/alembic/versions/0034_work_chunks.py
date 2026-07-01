"""Add the work_chunks table (HS1, chunk-level semantic search)

Creates ``work_chunks``: passage-level chunks of a work (title / abstract / TEI body sections) that
back chunk-level dense retrieval. ``work_id`` FK cascades on work delete (chunks belong to exactly
one work), a unique ``(work_id, position)`` keeps chunk order stable, and ``position`` is 0-based.
The per-model pgvector columns are added later by a Postgres-only migration (HS2); this table is
dialect-agnostic so it exists identically under the SQLite test path.

Revision ID: 0034_work_chunks
Revises: 0033_saved_filters
Create Date: 2026-07-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0034_work_chunks"
down_revision: str | None = "0033_saved_filters"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    op.create_table(
        "work_chunks",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "work_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("works.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("section", sa.String(length=255), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("work_id", "position", name="uq_work_chunk_position"),
    )
    op.create_index(op.f("ix_work_chunks_work_id"), "work_chunks", ["work_id"], unique=False)


def downgrade() -> None:
    """Revert the migration."""
    op.drop_index(op.f("ix_work_chunks_work_id"), table_name="work_chunks")
    op.drop_table("work_chunks")
