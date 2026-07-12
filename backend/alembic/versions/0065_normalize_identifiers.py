"""Normalize stored DOIs and arXiv base ids to their canonical forms (S3 backfill)

Data-only migration. Historical ingest paths wrote identifiers in whatever decoration they
arrived with (extraction stored the raw TEI DOI; ``identifiers.arxiv_base_id`` didn't lowercase),
while matching/enrichment normalize at compare time. Now that all write paths normalize (one
canonical parser), this backfill brings existing rows in line so exact-match joins
(``Work.arxiv_base_id ==``, ``Reference.doi ==``) stop missing legacy spellings.

Collision safety: ``works.doi`` / ``works.arxiv_base_id`` are uniquely indexed, so a row whose
normalized value already belongs to ANOTHER work is left untouched (those pairs are duplicate
papers — the duplicate-review queue's job, not a migration's). ``references.doi`` and
``external_papers.doi`` carry no unique constraint and are normalized unconditionally.

Downgrade is a no-op: the original decorated spellings are not preserved (normalization is the
canonical form going forward).

Revision ID: 0065_normalize_stored_identifiers
Revises: 0064_external_paper_local_match
Create Date: 2026-07-13
"""

import re
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0065_normalize_identifiers"
down_revision: str | None = "0064_external_paper_local_match"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Self-contained copies of the canonical normalizers (a migration must not drift with app code).
_DOI_SCHEME_PREFIXES = ("https://", "http://")
_DOI_HOST_PREFIXES = ("dx.doi.org/", "doi.org/", "doi:")


def _normalize_doi(doi: str) -> str:
    cleaned = doi.strip().lower()
    for prefix in _DOI_SCHEME_PREFIXES:
        cleaned = cleaned.removeprefix(prefix)
    for prefix in _DOI_HOST_PREFIXES:
        cleaned = cleaned.removeprefix(prefix)
    return cleaned


_ARXIV_VERSION_RE = re.compile(
    r"^(?P<base>(?:\d{4}\.\d{4,5})|(?:[a-z-]+(?:\.[A-Z]{2})?/\d{7}))(?:v\d+)?$",
    re.IGNORECASE,
)


def _normalize_arxiv_base(base: str) -> str:
    cleaned = base.strip().lower()
    match = _ARXIV_VERSION_RE.match(cleaned)
    return match.group("base") if match else cleaned


def _backfill_unique_column(conn, table: str, column: str, normalize) -> None:
    """Normalize a uniquely-indexed column, skipping rows whose target value is already taken."""
    rows = conn.execute(
        sa.text(f"SELECT id, {column} FROM {table} WHERE {column} IS NOT NULL")  # noqa: S608
    ).fetchall()
    taken = {value for _id, value in rows}
    for row_id, value in rows:
        normalized = normalize(value)
        if not normalized or normalized == value:
            continue
        if normalized in taken:
            # Another row already holds the canonical spelling — a duplicate pair for the
            # duplicate-review queue; leave this row as-is rather than violate the unique index.
            continue
        conn.execute(
            sa.text(f"UPDATE {table} SET {column} = :new WHERE id = :id"),  # noqa: S608
            {"new": normalized, "id": row_id},
        )
        taken.discard(value)
        taken.add(normalized)


def _backfill_plain_column(conn, table: str, column: str, normalize) -> None:
    """Normalize a non-unique column unconditionally."""
    rows = conn.execute(
        sa.text(f"SELECT id, {column} FROM {table} WHERE {column} IS NOT NULL")  # noqa: S608
    ).fetchall()
    for row_id, value in rows:
        normalized = normalize(value)
        if normalized and normalized != value:
            conn.execute(
                sa.text(f"UPDATE {table} SET {column} = :new WHERE id = :id"),  # noqa: S608
                {"new": normalized, "id": row_id},
            )


def upgrade() -> None:
    """Apply the migration."""
    conn = op.get_bind()
    _backfill_unique_column(conn, "works", "doi", _normalize_doi)
    _backfill_unique_column(conn, "works", "arxiv_base_id", _normalize_arxiv_base)
    _backfill_plain_column(conn, '"references"', "doi", _normalize_doi)  # reserved word — quote
    _backfill_plain_column(conn, "external_papers", "doi", _normalize_doi)


def downgrade() -> None:
    """No-op: normalization is the canonical form; original decorations are not preserved."""
