"""Add duplicate-merge shadow columns and the work_links table (Batch D)

``works.merged_into_id`` marks a work as a hidden shadow of the base it was merged into;
``works.merge_record`` stores the single-level reversal record for the most recent merge. The new
``work_links`` table backs the "Link" action's bidirectional related-works relationship.

Revision ID: 0053_work_merge_shadow
Revises: 0052_missing_work_decisions
Create Date: 2026-07-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0053_work_merge_shadow"
down_revision: str | None = "0052_missing_work_decisions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSONB = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    """Apply the migration."""
    op.add_column("works", sa.Column("merged_into_id", sa.Uuid(as_uuid=True), nullable=True))
    op.add_column("works", sa.Column("merge_record", _JSONB, nullable=True))
    op.create_index(op.f("ix_works_merged_into_id"), "works", ["merged_into_id"])
    op.create_foreign_key(
        "fk_works_merged_into_id",
        "works",
        "works",
        ["merged_into_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "work_links",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("work_a_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("work_b_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("link_type", sa.String(length=32), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["work_a_id"], ["works.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["work_b_id"], ["works.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("work_a_id", "work_b_id", "link_type", name="uq_work_link_pair"),
    )
    op.create_index(op.f("ix_work_links_work_a_id"), "work_links", ["work_a_id"])
    op.create_index(op.f("ix_work_links_work_b_id"), "work_links", ["work_b_id"])
    op.create_index(op.f("ix_work_links_created_by_user_id"), "work_links", ["created_by_user_id"])


def downgrade() -> None:
    """Revert the migration."""
    op.drop_index(op.f("ix_work_links_created_by_user_id"), table_name="work_links")
    op.drop_index(op.f("ix_work_links_work_b_id"), table_name="work_links")
    op.drop_index(op.f("ix_work_links_work_a_id"), table_name="work_links")
    op.drop_table("work_links")
    op.drop_constraint("fk_works_merged_into_id", "works", type_="foreignkey")
    op.drop_index(op.f("ix_works_merged_into_id"), table_name="works")
    op.drop_column("works", "merge_record")
    op.drop_column("works", "merged_into_id")
