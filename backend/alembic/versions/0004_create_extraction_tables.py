"""create extraction tables (references, citation mentions, metadata assertions)

Revision ID: 0004_extraction
Revises: 0003_m1_core_library
Create Date: 2026-06-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_extraction"
down_revision: str | None = "0003_m1_core_library"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    op.create_table(
        "references",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("citing_work_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("resolved_work_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("raw_citation", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("doi", sa.String(length=255), nullable=True),
        sa.Column("arxiv_id", sa.String(length=64), nullable=True),
        sa.Column("year", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_references_citing_work_id", "references", ["citing_work_id"])
    op.create_index("ix_references_resolved_work_id", "references", ["resolved_work_id"])
    op.create_index("ix_references_doi", "references", ["doi"])
    op.create_index("ix_references_arxiv_id", "references", ["arxiv_id"])

    op.create_table(
        "citation_mentions",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("citing_work_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("reference_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("resolved_cited_work_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("marker_text", sa.String(length=128), nullable=True),
        sa.Column("section_label", sa.String(length=255), nullable=True),
        sa.Column("context_before", sa.Text(), nullable=True),
        sa.Column("context_sentence", sa.Text(), nullable=True),
        sa.Column("context_after", sa.Text(), nullable=True),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("pdf_x", sa.Float(), nullable=True),
        sa.Column("pdf_y", sa.Float(), nullable=True),
        sa.Column("pdf_width", sa.Float(), nullable=True),
        sa.Column("pdf_height", sa.Float(), nullable=True),
    )
    op.create_index("ix_citation_mentions_citing_work_id", "citation_mentions", ["citing_work_id"])
    op.create_index("ix_citation_mentions_reference_id", "citation_mentions", ["reference_id"])
    op.create_index(
        "ix_citation_mentions_resolved_cited_work_id",
        "citation_mentions",
        ["resolved_cited_work_id"],
    )

    op.create_table(
        "metadata_assertions",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("entity_type", sa.String(length=64), nullable=False),
        sa.Column("entity_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("field_name", sa.String(length=128), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=128), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("selected_as_canonical", sa.Boolean(), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_metadata_assertions_entity_type", "metadata_assertions", ["entity_type"])
    op.create_index("ix_metadata_assertions_entity_id", "metadata_assertions", ["entity_id"])
    op.create_index("ix_metadata_assertions_field_name", "metadata_assertions", ["field_name"])
    op.create_index("ix_metadata_assertions_source", "metadata_assertions", ["source"])


def downgrade() -> None:
    """Revert the migration."""
    op.drop_table("metadata_assertions")
    op.drop_table("citation_mentions")
    op.drop_table("references")
