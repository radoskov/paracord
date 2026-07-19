"""Add summaries.promoted_at — when the user "set this version as current" (#22).

Summaries are ordered by COALESCE(promoted_at, created_at) desc so a promoted historical version
becomes the shown one without rewriting its original creation time.

Revision ID: 0084_summary_promoted_at
Revises: 0083_summary_fallback_reason
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0084_summary_promoted_at"
down_revision: str | None = "0083_summary_fallback_reason"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("summaries", sa.Column("promoted_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("summaries", "promoted_at")
