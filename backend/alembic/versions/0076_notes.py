"""Notes (2026-07-16): a free-form notes field on each work, and per-Insights-scope notes.

Revision ID: 0076_notes
Revises: 0075_tag_scope
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0076_notes"
down_revision: str | None = "0075_tag_scope"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("works", sa.Column("notes", sa.Text(), nullable=True))
    op.create_table(
        "scope_notes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("scope_type", sa.String(length=64), nullable=False),
        sa.Column("scope_id", sa.Uuid(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by_user_id", sa.Uuid(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("scope_type", "scope_id", name="uq_scope_note"),
    )
    op.create_index("ix_scope_notes_scope_type", "scope_notes", ["scope_type"])
    op.create_index("ix_scope_notes_scope_id", "scope_notes", ["scope_id"])


def downgrade() -> None:
    op.drop_index("ix_scope_notes_scope_id", table_name="scope_notes")
    op.drop_index("ix_scope_notes_scope_type", table_name="scope_notes")
    op.drop_table("scope_notes")
    op.drop_column("works", "notes")
