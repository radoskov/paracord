"""Add Summary provenance columns (§8.14.2, D31.2)

Summary generation already computed provider_requested / provider_used / fallback and the
source-section labels but only returned them transiently. Persist them, plus a content hash of the
stored text and the generating user/params, so a stored summary carries how it was produced.

Revision ID: 0048_summary_provenance
Revises: 0047_drop_full_ml_ocr_backend
Create Date: 2026-07-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0048_summary_provenance"
down_revision: str | None = "0047_drop_full_ml_ocr_backend"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    op.add_column("summaries", sa.Column("provider_requested", sa.String(length=64), nullable=True))
    op.add_column("summaries", sa.Column("provider_used", sa.String(length=64), nullable=True))
    op.add_column(
        "summaries",
        sa.Column("fallback", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("summaries", sa.Column("source_sections", sa.JSON(), nullable=True))
    op.add_column("summaries", sa.Column("content_hash", sa.String(length=64), nullable=True))
    op.add_column(
        "summaries", sa.Column("created_by_user_id", sa.Uuid(as_uuid=True), nullable=True)
    )
    op.add_column("summaries", sa.Column("params", sa.JSON(), nullable=True))


def downgrade() -> None:
    """Revert the migration."""
    op.drop_column("summaries", "params")
    op.drop_column("summaries", "created_by_user_id")
    op.drop_column("summaries", "content_hash")
    op.drop_column("summaries", "source_sections")
    op.drop_column("summaries", "fallback")
    op.drop_column("summaries", "provider_used")
    op.drop_column("summaries", "provider_requested")
