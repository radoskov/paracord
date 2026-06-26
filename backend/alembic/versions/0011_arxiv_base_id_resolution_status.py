"""Add arxiv_base_id to works, partial-unique DOI index, resolution_status to references

Revision ID: 0011_schema_identifiers
Revises: 0010_summaries_topics
Create Date: 2026-06-26

Changes
-------
* works.arxiv_base_id (String 64, nullable) — the version-less arXiv base id so that
  version-collapsing and duplicate detection can key on the stable base rather than
  reconstructing it at query time.  Backfilled from existing arxiv_id values via a
  simple regexp_replace on Postgres.
* Partial unique index on works.arxiv_base_id WHERE NOT NULL (prevents two works from
  claiming the same arXiv paper independently of version).
* Replace the plain works.doi index with a partial unique index WHERE NOT NULL (prevents
  two works sharing a DOI — an invariant the spec requires).
* references.resolution_status (String 32, default 'unresolved') — the edge-classification
  enum required by §12.5 for the citation-graph pipeline.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_schema_identifiers"
down_revision: str | None = "0010_summaries_topics"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    # --- works: arxiv_base_id column + backfill + partial unique indexes ----------

    op.add_column("works", sa.Column("arxiv_base_id", sa.String(length=64), nullable=True))
    op.create_index("ix_works_arxiv_base_id", "works", ["arxiv_base_id"])

    # Backfill: strip leading prefixes and trailing version suffix from arxiv_id.
    # The regexp_replace calls are Postgres-specific; the migration self-skips on SQLite
    # (SQLite test databases are built from Base.metadata.create_all and don't run migrations).
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        bind.execute(
            sa.text(
                """
                UPDATE works
                SET arxiv_base_id = regexp_replace(
                    regexp_replace(
                        regexp_replace(arxiv_id, '^https?://arxiv\\.org/abs/', ''),
                        '^arXiv:', ''
                    ),
                    'v[0-9]+$', ''
                )
                WHERE arxiv_id IS NOT NULL
                """
            )
        )
        # Partial unique index: only enforce uniqueness for non-null values so that
        # works without an arXiv id are not affected.
        op.create_index(
            "uq_works_arxiv_base_id",
            "works",
            ["arxiv_base_id"],
            unique=True,
            postgresql_where=sa.text("arxiv_base_id IS NOT NULL"),
        )

        # Replace the plain doi index with a partial unique one.
        op.drop_index("ix_works_doi", table_name="works")
        op.create_index(
            "uq_works_doi",
            "works",
            ["doi"],
            unique=True,
            postgresql_where=sa.text("doi IS NOT NULL"),
        )

    # --- references: resolution_status column ------------------------------------

    op.add_column(
        "references",
        sa.Column(
            "resolution_status",
            sa.String(length=32),
            server_default="unresolved",
            nullable=False,
        ),
    )
    op.create_index("ix_references_resolution_status", "references", ["resolution_status"])


def downgrade() -> None:
    """Revert the migration."""
    op.drop_index("ix_references_resolution_status", table_name="references")
    op.drop_column("references", "resolution_status")

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.drop_index("uq_works_doi", table_name="works")
        op.create_index("ix_works_doi", "works", ["doi"])
        op.drop_index("uq_works_arxiv_base_id", table_name="works")

    op.drop_index("ix_works_arxiv_base_id", table_name="works")
    op.drop_column("works", "arxiv_base_id")
