"""Canonical shared references + per-work citation links (batch 12)

Splits the per-citing-work ``references`` rows into a **canonical** reference (the cited thing) plus a
``reference_citations`` link table (which works cite it), mirroring the incoming
``external_papers``/``external_citations`` model. Also adds the reference-matching columns
(``suggested_work_id``, ``match_score``, ``normalized_title``, ``authors``, ``dedup_key``).

This migration is a **lossless 1:1 structural expansion**: every existing reference keeps its row and
id and gets exactly one link row (its old ``citing_work_id`` + ``source_tei_id``). No reference is
merged or deleted here — deduplication is a separate, forward-only operation. ``resolved_work_id`` /
``resolution_status`` / citation mentions are untouched.

Revision ID: 0059_canonical_references
Revises: 0058_external_papers_normalize
Create Date: 2026-07-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from app.services.reference_links import reference_dedup_key
from app.utils.normalization import normalize_title
from sqlalchemy.dialects import postgresql

revision: str = "0059_canonical_references"
down_revision: str | None = "0058_external_papers_normalize"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    # 1) The per-citing-work link table.
    op.create_table(
        "reference_citations",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("reference_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("citing_work_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("source_tei_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["reference_id"], ["references.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["citing_work_id"], ["works.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_tei_id"], ["raw_tei_documents.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("reference_id", "citing_work_id", name="uq_reference_citation"),
    )
    op.create_index(
        op.f("ix_reference_citations_reference_id"), "reference_citations", ["reference_id"]
    )
    op.create_index(
        op.f("ix_reference_citations_citing_work_id"), "reference_citations", ["citing_work_id"]
    )
    op.create_index(
        op.f("ix_reference_citations_source_tei_id"), "reference_citations", ["source_tei_id"]
    )

    # 2) Lossless 1:1 backfill — one link per existing reference (server-side UUIDs, set-based).
    op.execute(
        sa.text(
            "INSERT INTO reference_citations "
            "(id, reference_id, citing_work_id, source_tei_id, created_at) "
            "SELECT gen_random_uuid(), id, citing_work_id, source_tei_id, created_at "
            'FROM "references"'
        )
    )

    # 3) New matching/identity columns on the (now canonical) references table.
    op.add_column("references", sa.Column("normalized_title", sa.Text(), nullable=True))
    op.add_column(
        "references",
        sa.Column(
            "suggested_work_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("works.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column("references", sa.Column("match_score", sa.Float(), nullable=True))
    op.add_column("references", sa.Column("authors", postgresql.JSONB(), nullable=True))
    op.add_column("references", sa.Column("dedup_key", sa.String(length=512), nullable=True))

    # 4) Backfill normalized_title + dedup_key with the real helpers (exact; batched executemany).
    conn = op.get_bind()
    rows = conn.execute(
        sa.text('SELECT id, title, doi, arxiv_id, year FROM "references"')
    ).fetchall()
    params: list[dict] = []
    for row in rows:
        nt = normalize_title(row.title) if row.title else None
        nt = nt or None
        key = reference_dedup_key(
            doi=row.doi, arxiv_id=row.arxiv_id, normalized_title=nt, year=row.year
        )
        params.append({"id": row.id, "nt": nt, "key": key})
    if params:
        conn.execute(
            sa.text(
                'UPDATE "references" SET normalized_title = :nt, dedup_key = :key WHERE id = :id'
            ),
            params,
        )

    op.create_index(op.f("ix_references_normalized_title"), "references", ["normalized_title"])
    op.create_index(op.f("ix_references_suggested_work_id"), "references", ["suggested_work_id"])
    op.create_index(op.f("ix_references_dedup_key"), "references", ["dedup_key"])

    # 5) Drop the moved columns (now carried by the link table).
    op.drop_column("references", "citing_work_id")
    op.drop_column("references", "source_tei_id")


def downgrade() -> None:
    """Revert the migration.

    Reconstructs the single ``citing_work_id`` / ``source_tei_id`` from each reference's (single, in
    the un-consolidated state) link row. Only clean before any reference consolidation has merged
    links from several works onto one reference.
    """
    op.add_column(
        "references",
        sa.Column(
            "citing_work_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("works.id", ondelete="CASCADE"),
            nullable=True,
        ),
    )
    op.add_column(
        "references",
        sa.Column(
            "source_tei_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("raw_tei_documents.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.execute(
        sa.text(
            'UPDATE "references" AS r SET citing_work_id = rc.citing_work_id, '
            "source_tei_id = rc.source_tei_id "
            "FROM reference_citations AS rc WHERE rc.reference_id = r.id"
        )
    )
    # Drop references left with no link (would violate the NOT NULL restored below) — none expected.
    op.execute(sa.text('DELETE FROM "references" WHERE citing_work_id IS NULL'))
    op.alter_column("references", "citing_work_id", nullable=False)
    op.create_index(op.f("ix_references_citing_work_id"), "references", ["citing_work_id"])
    op.create_index(op.f("ix_references_source_tei_id"), "references", ["source_tei_id"])

    op.drop_index(op.f("ix_references_dedup_key"), table_name="references")
    op.drop_index(op.f("ix_references_suggested_work_id"), table_name="references")
    op.drop_index(op.f("ix_references_normalized_title"), table_name="references")
    op.drop_column("references", "dedup_key")
    op.drop_column("references", "authors")
    op.drop_column("references", "match_score")
    op.drop_column("references", "suggested_work_id")
    op.drop_column("references", "normalized_title")

    op.drop_index(op.f("ix_reference_citations_source_tei_id"), table_name="reference_citations")
    op.drop_index(op.f("ix_reference_citations_citing_work_id"), table_name="reference_citations")
    op.drop_index(op.f("ix_reference_citations_reference_id"), table_name="reference_citations")
    op.drop_table("reference_citations")
