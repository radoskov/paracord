"""Add missing_work_decisions (Track C C3a citation-summary worklist)

Per-user import/ignore decisions on frequently-cited-but-missing works, keyed by the stable
normalized missing-work key so a decision survives a summary recompute / re-extraction.

Revision ID: 0052_missing_work_decisions
Revises: 0051_custom_themes
Create Date: 2026-07-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0052_missing_work_decisions"
down_revision: str | None = "0051_custom_themes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    op.create_table(
        "missing_work_decisions",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("missing_key", sa.String(length=512), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "missing_key", name="uq_missing_decision_user_key"),
    )
    op.create_index(
        op.f("ix_missing_work_decisions_user_id"),
        "missing_work_decisions",
        ["user_id"],
    )


def downgrade() -> None:
    """Revert the migration."""
    op.drop_index(op.f("ix_missing_work_decisions_user_id"), table_name="missing_work_decisions")
    op.drop_table("missing_work_decisions")
