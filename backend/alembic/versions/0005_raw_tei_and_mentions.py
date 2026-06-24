"""store raw TEI and link citation mentions

Revision ID: 0005_raw_tei_mentions
Revises: 0004_extraction
Create Date: 2026-06-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005_raw_tei_mentions"
down_revision: str | None = "0004_extraction"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    op.create_table(
        "raw_tei_documents",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("file_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("work_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("source", sa.String(length=128), nullable=False),
        sa.Column("tei_xml", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_raw_tei_documents_file_id", "raw_tei_documents", ["file_id"])
    op.create_index("ix_raw_tei_documents_work_id", "raw_tei_documents", ["work_id"])
    op.create_index("ix_raw_tei_documents_source", "raw_tei_documents", ["source"])
    op.create_index("ix_raw_tei_documents_created_at", "raw_tei_documents", ["created_at"])

    op.add_column("references", sa.Column("source_tei_id", sa.Uuid(as_uuid=True), nullable=True))
    op.create_index("ix_references_source_tei_id", "references", ["source_tei_id"])

    op.add_column(
        "citation_mentions",
        sa.Column("source_tei_id", sa.Uuid(as_uuid=True), nullable=True),
    )
    op.add_column("citation_mentions", sa.Column("created_at", sa.DateTime(), nullable=True))
    op.execute("UPDATE citation_mentions SET created_at = now() WHERE created_at IS NULL")
    op.alter_column("citation_mentions", "created_at", nullable=False)
    op.create_index("ix_citation_mentions_source_tei_id", "citation_mentions", ["source_tei_id"])


def downgrade() -> None:
    """Revert the migration."""
    op.drop_index("ix_citation_mentions_source_tei_id", table_name="citation_mentions")
    op.drop_column("citation_mentions", "created_at")
    op.drop_column("citation_mentions", "source_tei_id")
    op.drop_index("ix_references_source_tei_id", table_name="references")
    op.drop_column("references", "source_tei_id")
    op.drop_index("ix_raw_tei_documents_created_at", table_name="raw_tei_documents")
    op.drop_index("ix_raw_tei_documents_source", table_name="raw_tei_documents")
    op.drop_index("ix_raw_tei_documents_work_id", table_name="raw_tei_documents")
    op.drop_index("ix_raw_tei_documents_file_id", table_name="raw_tei_documents")
    op.drop_table("raw_tei_documents")
