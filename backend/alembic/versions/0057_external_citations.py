"""Add external_citations (batch 10, issue 8: incoming external citing papers)

Caches the papers (from OpenAlex / Semantic Scholar) that cite a work, fetched on demand. Drives the
paper-view "Citing papers" panel and the incoming side of the reference graph.

Revision ID: 0057_external_citations
Revises: 0056_import_staging
Create Date: 2026-07-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0057_external_citations"
down_revision: str | None = "0056_import_staging"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    op.create_table(
        "external_citations",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("work_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("authors", sa.Text(), nullable=True),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("doi", sa.String(length=255), nullable=True),
        sa.Column("venue", sa.Text(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["work_id"], ["works.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_external_citations_work_id"), "external_citations", ["work_id"])
    op.create_index(op.f("ix_external_citations_source"), "external_citations", ["source"])
    op.create_index(op.f("ix_external_citations_doi"), "external_citations", ["doi"])


def downgrade() -> None:
    """Revert the migration."""
    op.drop_index(op.f("ix_external_citations_doi"), table_name="external_citations")
    op.drop_index(op.f("ix_external_citations_source"), table_name="external_citations")
    op.drop_index(op.f("ix_external_citations_work_id"), table_name="external_citations")
    op.drop_table("external_citations")
