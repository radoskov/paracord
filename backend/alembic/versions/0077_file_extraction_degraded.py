"""Degraded-extraction flag on files (2026-07-17): set when the stored TEI came from the GROBID
header+references fallback (full-text parser crashed on the PDF). Drives the UI badge.

Revision ID: 0077_file_extraction_degraded
Revises: 0076_notes
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0077_file_extraction_degraded"
down_revision: str | None = "0076_notes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "files",
        sa.Column("extraction_degraded", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("files", "extraction_degraded")
