"""Add files.extraction_attempts (F2 — bounded transient-extraction retries)

Durable per-file attempt counter. Incremented at the start of each extraction run and reset by a
user-initiated (re-)extract; when it reaches the cap a transient failure is treated as terminal so
automatic retries + the recovery sweep can't loop forever across restarts. NOT NULL, server default
0 so existing rows backfill to 0.

Revision ID: 0062_file_extraction_attempts
Revises: 0061_reference_rescan_on_startup
Create Date: 2026-07-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0062_file_extraction_attempts"
down_revision: str | None = "0061_reference_rescan_on_startup"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    op.add_column(
        "files",
        sa.Column("extraction_attempts", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    """Revert the migration."""
    op.drop_column("files", "extraction_attempts")
