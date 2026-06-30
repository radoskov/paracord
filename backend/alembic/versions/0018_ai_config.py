"""Owner-managed runtime AI provider configuration (WORKPLAN_NEXT Stage 8)

Single-row ``ai_config`` table overlaying the static Settings defaults. NULL columns fall back to
those defaults, so an empty table reproduces the out-of-the-box lexical-baseline behavior.

Revision ID: 0018_ai_config
Revises: 0017_fk_and_jsonb_hardening
Create Date: 2026-06-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018_ai_config"
down_revision: str | None = "0017_fk_and_jsonb_hardening"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    op.create_table(
        "ai_config",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("embedding_provider", sa.String(length=64), nullable=True),
        sa.Column("embedding_model", sa.String(length=255), nullable=True),
        sa.Column("summary_provider", sa.String(length=64), nullable=True),
        sa.Column("summary_model", sa.String(length=255), nullable=True),
        sa.Column("topic_backend", sa.String(length=64), nullable=True),
        sa.Column("topic_embedding_model", sa.String(length=255), nullable=True),
        sa.Column("ollama_url", sa.String(length=512), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by_user_id", sa.Uuid(as_uuid=True), nullable=True),
    )


def downgrade() -> None:
    """Revert the migration."""
    op.drop_table("ai_config")
