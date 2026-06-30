"""Deterministic extraction keywords on works (SPEC §8.15.1)

Adds ``works.keywords`` (JSONB list of keyphrases), populated on extraction.

Revision ID: 0023_work_keywords
Revises: 0022_reading_queue_position
Create Date: 2026-06-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0023_work_keywords"
down_revision: str | None = "0022_reading_queue_position"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSONB = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    """Apply the migration."""
    op.add_column("works", sa.Column("keywords", _JSONB, nullable=True))


def downgrade() -> None:
    """Revert the migration."""
    op.drop_column("works", "keywords")
