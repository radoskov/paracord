"""Add files.extraction_requested_at durable owed-extraction marker (D7)

Records that a file is *owed* an extraction: set in the same commit that queues extraction, cleared
by the extraction worker on terminal success or failure. The startup recovery sweep re-enqueues
files whose marker is still set, so an enqueue lost to a dead Redis is picked up automatically.

Revision ID: 0042_file_extraction_owed
Revises: 0041_papers_per_page
Create Date: 2026-07-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0042_file_extraction_owed"
down_revision: str | None = "0041_papers_per_page"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    op.add_column(
        "files",
        sa.Column("extraction_requested_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Revert the migration."""
    op.drop_column("files", "extraction_requested_at")
