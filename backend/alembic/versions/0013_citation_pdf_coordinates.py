"""Replace scalar citation_mention pdf_* columns with a pdf_coordinates JSONB

Revision ID: 0013_citation_pdf_coordinates
Revises: 0012_normalize_dois
Create Date: 2026-06-29

Changes
-------
* citation_mentions: drop the four scalar coordinate columns (pdf_x, pdf_y, pdf_width,
  pdf_height) and add a single ``pdf_coordinates`` JSONB column holding a list of boxes
  ``[{"page", "x", "y", "w", "h"}, ...]``.  A mention can wrap across lines, so a single
  box can't represent it; the list form matches SPEC §9.3 and feeds the PDF.js reader's
  multi-quad highlight anchors.  The four columns were always NULL in practice (no
  coordinate extraction existed yet), so no data migration is required.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013_citation_pdf_coordinates"
down_revision: str | None = "0012_normalize_dois"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    op.add_column(
        "citation_mentions",
        sa.Column("pdf_coordinates", postgresql.JSONB(), nullable=True),
    )
    for column in ("pdf_x", "pdf_y", "pdf_width", "pdf_height"):
        op.drop_column("citation_mentions", column)


def downgrade() -> None:
    """Revert the migration."""
    for column in ("pdf_x", "pdf_y", "pdf_width", "pdf_height"):
        op.add_column("citation_mentions", sa.Column(column, sa.Float(), nullable=True))
    op.drop_column("citation_mentions", "pdf_coordinates")
