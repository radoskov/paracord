"""Add the saved_filters table (Phase B7, per-user saved library filters)

Creates ``saved_filters``: a user-owned, named Library query (free-text + search mode + a
structured ``params`` JSONB blob) usable as a filter and as a graph/export scope. The
``owner_user_id`` FK cascades on user delete (a filter belongs to exactly one user); a unique
constraint on ``(owner_user_id, name)`` backs the create-duplicate 409. ``params`` is created
NOT NULL directly (no existing rows to backfill; the ORM supplies the ``{}`` default).

Revision ID: 0033_saved_filters
Revises: 0032_graph_scopes_version_group
Create Date: 2026-07-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0033_saved_filters"
down_revision: str | None = "0032_graph_scopes_version_group"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    op.create_table(
        "saved_filters",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "owner_user_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("search_mode", sa.String(length=16), nullable=False),
        sa.Column("query_text", sa.Text(), nullable=True),
        sa.Column(
            "params",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("owner_user_id", "name", name="uq_saved_filter_owner_name"),
    )
    op.create_index(
        op.f("ix_saved_filters_owner_user_id"),
        "saved_filters",
        ["owner_user_id"],
        unique=False,
    )


def downgrade() -> None:
    """Revert the migration."""
    op.drop_index(op.f("ix_saved_filters_owner_user_id"), table_name="saved_filters")
    op.drop_table("saved_filters")
