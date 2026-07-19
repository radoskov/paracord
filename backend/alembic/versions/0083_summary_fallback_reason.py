"""Persist summaries.fallback_reason — the human-readable reason a summary degraded to the
extractive fallback, so the paper view can explain it for stored/re-listed summaries (not just on a
fresh generation).

Revision ID: 0083_summary_fallback_reason
Revises: 0082_ai_llm_timeout_reasoning
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0083_summary_fallback_reason"
down_revision: str | None = "0082_ai_llm_timeout_reasoning"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("summaries", sa.Column("fallback_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("summaries", "fallback_reason")
