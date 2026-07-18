"""Rows grouping layer (2026-07-18): a new broadest layer that contains racks (Row ⊃ Rack ⊃ Shelf
⊃ Paper). `rows` mirrors `racks`; `row_racks` mirrors `rack_shelves` one hop up; `tag_rows` mirrors
`tag_racks`. A paper's row membership is inferred work→shelf→rack→row.

Revision ID: 0078_rows
Revises: 0077_file_extraction_degraded
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0078_rows"
down_revision: str | None = "0077_file_extraction_degraded"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "rows",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("access_level", sa.String(length=16), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_rows_created_by_user_id"), "rows", ["created_by_user_id"], unique=False
    )
    op.create_index(op.f("ix_rows_name"), "rows", ["name"], unique=False)
    op.create_index(op.f("ix_rows_status"), "rows", ["status"], unique=False)

    op.create_table(
        "row_racks",
        sa.Column("row_id", sa.Uuid(), nullable=False),
        sa.Column("rack_id", sa.Uuid(), nullable=False),
        sa.Column("added_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("position", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["row_id"], ["rows.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["rack_id"], ["racks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("row_id", "rack_id"),
    )
    op.create_index(
        op.f("ix_row_racks_added_by_user_id"), "row_racks", ["added_by_user_id"], unique=False
    )
    op.create_index(op.f("ix_row_racks_rack_id"), "row_racks", ["rack_id"], unique=False)

    op.create_table(
        "tag_rows",
        sa.Column("tag_id", sa.Uuid(), nullable=False),
        sa.Column("row_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["tag_id"], ["tags.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["row_id"], ["rows.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("tag_id", "row_id"),
    )
    op.create_index(op.f("ix_tag_rows_row_id"), "tag_rows", ["row_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_tag_rows_row_id"), table_name="tag_rows")
    op.drop_table("tag_rows")
    op.drop_index(op.f("ix_row_racks_rack_id"), table_name="row_racks")
    op.drop_index(op.f("ix_row_racks_added_by_user_id"), table_name="row_racks")
    op.drop_table("row_racks")
    op.drop_index(op.f("ix_rows_status"), table_name="rows")
    op.drop_index(op.f("ix_rows_name"), table_name="rows")
    op.drop_index(op.f("ix_rows_created_by_user_id"), table_name="rows")
    op.drop_table("rows")
