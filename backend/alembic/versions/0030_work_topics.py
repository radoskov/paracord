"""Per-paper topic terms on works (SPEC §8.15, Phase K)

Adds ``works.topics`` (JSONB list of representative topic terms), populated on demand by the
per-paper Topic action. Mirrors ``works.keywords`` (0023).

Revision ID: 0030_work_topics
Revises: 0029_access_control_backfill
Create Date: 2026-07-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0030_work_topics"
down_revision: str | None = "0029_access_control_backfill"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSONB = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    """Apply the migration."""
    op.add_column("works", sa.Column("topics", _JSONB, nullable=True))


def downgrade() -> None:
    """Revert the migration."""
    op.drop_column("works", "topics")
