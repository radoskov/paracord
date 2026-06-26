"""Normalize existing DOI values: strip URL prefix and lowercase

Revision ID: 0012_normalize_dois
Revises: 0011_schema_identifiers
Create Date: 2026-06-26

Changes
-------
* Normalizes works.doi values in-place: strips 'https://doi.org/' and 'doi:' prefixes
  and lowercases the remainder.  New writes already normalize at the application layer;
  this migration catches any rows imported before that constraint was added.
"""

from alembic import op
from sqlalchemy import text

revision = "0012_normalize_dois"
down_revision = "0011_schema_identifiers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    dialect = conn.dialect.name

    if dialect == "postgresql":
        conn.execute(
            text(
                """
                UPDATE works
                SET doi = LOWER(
                    regexp_replace(
                        regexp_replace(doi, '^https?://doi\\.org/', ''),
                        '^doi:', '', 'i'
                    )
                )
                WHERE doi IS NOT NULL
                  AND (
                      doi ILIKE 'https://doi.org/%'
                      OR doi ILIKE 'http://doi.org/%'
                      OR doi ILIKE 'doi:%'
                      OR doi != LOWER(doi)
                  )
                """
            )
        )
    else:
        # SQLite: process row-by-row (only needed for test/dev databases).
        rows = conn.execute(text("SELECT id, doi FROM works WHERE doi IS NOT NULL")).fetchall()
        for row_id, doi in rows:
            normalized = doi.strip().lower()
            if normalized.startswith("https://doi.org/"):
                normalized = normalized[len("https://doi.org/") :]
            elif normalized.startswith("http://doi.org/"):
                normalized = normalized[len("http://doi.org/") :]
            elif normalized.startswith("doi:"):
                normalized = normalized[len("doi:") :]
            if normalized != doi:
                conn.execute(
                    text("UPDATE works SET doi = :doi WHERE id = :id"),
                    {"doi": normalized, "id": row_id},
                )


def downgrade() -> None:
    pass  # DOI normalization is not reversible
