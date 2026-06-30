"""Add deferred FKs (C3) and convert remaining JSON columns to JSONB (C4)

Adds the previously-weak foreign keys on ``locations.agent_id``, ``references.*`` and
``citation_mentions.*`` (with appropriate CASCADE / SET NULL semantics) and converts the remaining
document-shaped JSON columns to JSONB on Postgres so they support ``@>`` / ``->`` querying.

Postgres-only: the column type is a ``JSON().with_variant(JSONB(), "postgresql")`` in the models, so
on SQLite (used only by the in-memory test schema, built from metadata rather than migrations) both
resolve to JSON and there is nothing to migrate. The body therefore no-ops on non-Postgres dialects.

Revision ID: 0017_fk_and_jsonb_hardening
Revises: 0016_agent_file_actions
Create Date: 2026-06-30
"""

from collections.abc import Sequence

from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0017_fk_and_jsonb_hardening"
down_revision: str | None = "0016_agent_file_actions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (constraint_name, table, local_col, target_table, target_col, ondelete)
_FOREIGN_KEYS = [
    ("fk_locations_agent_id", "locations", "agent_id", "agents", "id", "SET NULL"),
    ("fk_references_citing_work", "references", "citing_work_id", "works", "id", "CASCADE"),
    ("fk_references_resolved_work", "references", "resolved_work_id", "works", "id", "SET NULL"),
    (
        "fk_references_source_tei",
        "references",
        "source_tei_id",
        "raw_tei_documents",
        "id",
        "SET NULL",
    ),
    ("fk_mentions_citing_work", "citation_mentions", "citing_work_id", "works", "id", "CASCADE"),
    ("fk_mentions_reference", "citation_mentions", "reference_id", "references", "id", "CASCADE"),
    (
        "fk_mentions_resolved_work",
        "citation_mentions",
        "resolved_cited_work_id",
        "works",
        "id",
        "SET NULL",
    ),
    (
        "fk_mentions_source_tei",
        "citation_mentions",
        "source_tei_id",
        "raw_tei_documents",
        "id",
        "SET NULL",
    ),
]

# (table, column) JSON -> JSONB conversions.
_JSONB_COLUMNS = [
    ("sources", "config"),
    ("import_batches", "settings"),
    ("import_batches", "stats"),
    ("duplicate_candidates", "signals"),
    ("annotations", "coordinates"),
]


def upgrade() -> None:
    """Apply the migration (Postgres only)."""
    if op.get_bind().dialect.name != "postgresql":
        return
    for name, table, col, target, target_col, ondelete in _FOREIGN_KEYS:
        op.create_foreign_key(name, table, target, [col], [target_col], ondelete=ondelete)
    for table, col in _JSONB_COLUMNS:
        op.alter_column(
            table,
            col,
            type_=postgresql.JSONB(),
            postgresql_using=f"{col}::jsonb",
            existing_nullable=True,
        )


def downgrade() -> None:
    """Revert the migration (Postgres only)."""
    if op.get_bind().dialect.name != "postgresql":
        return
    from sqlalchemy import JSON

    for table, col in _JSONB_COLUMNS:
        op.alter_column(
            table,
            col,
            type_=JSON(),
            postgresql_using=f"{col}::json",
            existing_nullable=True,
        )
    for name, table, *_ in _FOREIGN_KEYS:
        op.drop_constraint(name, table, type_="foreignkey")
