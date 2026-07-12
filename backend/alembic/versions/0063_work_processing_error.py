"""Add works.processing_error (F2 — loud per-paper failures for non-recovered stages)

A short "<stage>: <reason>" set by a failed enrich/keyword/topic background job and cleared on that
same stage's next success, so the failure is visible on the paper itself (a badge) rather than only
in the global Jobs/Events views. Nullable; NULL means no outstanding error.

Revision ID: 0063_work_processing_error
Revises: 0062_file_extraction_attempts
Create Date: 2026-07-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0063_work_processing_error"
down_revision: str | None = "0062_file_extraction_attempts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    op.add_column("works", sa.Column("processing_error", sa.Text(), nullable=True))


def downgrade() -> None:
    """Revert the migration."""
    op.drop_column("works", "processing_error")
