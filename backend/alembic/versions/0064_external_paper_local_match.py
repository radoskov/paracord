"""Match external citing papers to local works (incoming direction of the local matcher)

Adds to ``external_papers``:

* ``resolved_work_id`` — the library work this citing paper IS, when the local matcher finds one
  (identifier match, or fuzzy passing the same title/year/author gates as reference matching).
  SET NULL on work deletion; repointed on merge like `Reference.resolved_work_id`.
* ``arxiv_id`` — citing papers fetched from Semantic Scholar carry an arXiv id in externalIds;
  storing it lets identifier matching work for arXiv-only citing papers.

Both nullable + additive: safe on a live database, existing rows are backfilled lazily by the next
citing-papers fetch or the library-wide rescan job.

Revision ID: 0064_external_paper_local_match
Revises: 0063_work_processing_error
Create Date: 2026-07-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0064_external_paper_local_match"
down_revision: str | None = "0063_work_processing_error"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    op.add_column("external_papers", sa.Column("arxiv_id", sa.String(length=64), nullable=True))
    op.add_column(
        "external_papers",
        sa.Column("resolved_work_id", sa.Uuid(), nullable=True),
    )
    op.create_index(
        "ix_external_papers_resolved_work_id", "external_papers", ["resolved_work_id"]
    )
    op.create_foreign_key(
        "fk_external_papers_resolved_work_id",
        "external_papers",
        "works",
        ["resolved_work_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    """Revert the migration."""
    op.drop_constraint(
        "fk_external_papers_resolved_work_id", "external_papers", type_="foreignkey"
    )
    op.drop_index("ix_external_papers_resolved_work_id", table_name="external_papers")
    op.drop_column("external_papers", "resolved_work_id")
    op.drop_column("external_papers", "arxiv_id")
