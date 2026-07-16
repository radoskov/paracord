"""Tag scoping (2026-07-16): tags can be restricted to shelves/racks (no rows = global). Two join
tables, distinct from tag_links (which record actual taggings).

Revision ID: 0075_tag_scope
Revises: 0074_summary_effort_levels
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0075_tag_scope"
down_revision: str | None = "0074_summary_effort_levels"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tag_shelves",
        sa.Column("tag_id", sa.Uuid(), nullable=False),
        sa.Column("shelf_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["tag_id"], ["tags.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["shelf_id"], ["shelves.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("tag_id", "shelf_id"),
    )
    op.create_index("ix_tag_shelves_shelf_id", "tag_shelves", ["shelf_id"])
    op.create_table(
        "tag_racks",
        sa.Column("tag_id", sa.Uuid(), nullable=False),
        sa.Column("rack_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["tag_id"], ["tags.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["rack_id"], ["racks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("tag_id", "rack_id"),
    )
    op.create_index("ix_tag_racks_rack_id", "tag_racks", ["rack_id"])


def downgrade() -> None:
    op.drop_index("ix_tag_racks_rack_id", table_name="tag_racks")
    op.drop_table("tag_racks")
    op.drop_index("ix_tag_shelves_shelf_id", table_name="tag_shelves")
    op.drop_table("tag_shelves")
