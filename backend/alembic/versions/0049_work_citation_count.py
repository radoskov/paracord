"""Add Work citation-count snapshot columns (Track C P1, viz prerequisite)

Enrichment already queries Crossref / OpenAlex / Semantic Scholar, each of which returns an external
citation count. Cache the highest-priority one on the work (with its source and fetch time) so the
paper view and the upcoming visualization module can read impact without a live call.

Revision ID: 0049_work_citation_count
Revises: 0048_summary_provenance
Create Date: 2026-07-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0049_work_citation_count"
down_revision: str | None = "0048_summary_provenance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    op.add_column("works", sa.Column("citation_count", sa.Integer(), nullable=True))
    op.add_column("works", sa.Column("citation_count_source", sa.String(length=32), nullable=True))
    op.add_column(
        "works",
        sa.Column("citation_count_fetched_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Revert the migration."""
    op.drop_column("works", "citation_count_fetched_at")
    op.drop_column("works", "citation_count_source")
    op.drop_column("works", "citation_count")
