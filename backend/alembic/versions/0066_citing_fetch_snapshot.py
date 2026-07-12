"""Add works.citing_fetched_at/_source — the citing-papers fetch snapshot (S12)

An authoritative "zero citing papers" answer has no ExternalCitationLink rows to carry a
timestamp, so before this the system could not distinguish "fetched, genuinely zero" from
"never fetched / fetch failed" — a stale cached list survived forever once a paper's citers
went away. The per-work snapshot records when and via which provider the list was last
authoritatively replaced (including with an empty result).

Additive + nullable: safe on a live database; existing rows backfill on their next fetch.

Revision ID: 0066_citing_fetch_snapshot
Revises: 0065_normalize_identifiers
Create Date: 2026-07-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0066_citing_fetch_snapshot"
down_revision: str | None = "0065_normalize_identifiers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    op.add_column(
        "works", sa.Column("citing_fetched_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("works", sa.Column("citing_fetched_source", sa.String(length=32), nullable=True))


def downgrade() -> None:
    """Revert the migration."""
    op.drop_column("works", "citing_fetched_source")
    op.drop_column("works", "citing_fetched_at")
