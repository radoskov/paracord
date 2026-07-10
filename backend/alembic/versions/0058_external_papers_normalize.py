"""Normalize external citations into dedup'd external_papers + link table (batch 10, issue 8)

Replaces the denormalized ``external_citations`` (one row per citing-paper × cited-work, added in
0057) with ``external_papers`` (a deduplicated "quasi-paper", metadata only) and
``external_citation_links`` (external_paper ⇄ work), so a citing paper shared by several works is
stored once and only referenced. No production data exists yet, so 0057's table is simply dropped.

Revision ID: 0058_external_papers_normalize
Revises: 0057_external_citations
Create Date: 2026-07-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0058_external_papers_normalize"
down_revision: str | None = "0057_external_citations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    # Drop the denormalized table from 0057 (pre-release, no data to preserve).
    op.drop_index(op.f("ix_external_citations_doi"), table_name="external_citations")
    op.drop_index(op.f("ix_external_citations_source"), table_name="external_citations")
    op.drop_index(op.f("ix_external_citations_work_id"), table_name="external_citations")
    op.drop_table("external_citations")

    op.create_table(
        "external_papers",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("dedup_key", sa.String(length=255), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column("doi", sa.String(length=255), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("authors", sa.Text(), nullable=True),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("venue", sa.Text(), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_external_papers_dedup_key"), "external_papers", ["dedup_key"], unique=True
    )
    op.create_index(op.f("ix_external_papers_doi"), "external_papers", ["doi"])

    op.create_table(
        "external_citation_links",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("external_paper_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("work_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["external_paper_id"], ["external_papers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["work_id"], ["works.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("external_paper_id", "work_id", name="uq_external_citation_link"),
    )
    op.create_index(
        op.f("ix_external_citation_links_external_paper_id"),
        "external_citation_links",
        ["external_paper_id"],
    )
    op.create_index(
        op.f("ix_external_citation_links_work_id"), "external_citation_links", ["work_id"]
    )


def downgrade() -> None:
    """Revert the migration."""
    op.drop_index(op.f("ix_external_citation_links_work_id"), table_name="external_citation_links")
    op.drop_index(
        op.f("ix_external_citation_links_external_paper_id"),
        table_name="external_citation_links",
    )
    op.drop_table("external_citation_links")
    op.drop_index(op.f("ix_external_papers_doi"), table_name="external_papers")
    op.drop_index(op.f("ix_external_papers_dedup_key"), table_name="external_papers")
    op.drop_table("external_papers")

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
