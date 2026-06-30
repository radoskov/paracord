"""Reading-queue manual ordering (SPEC §8.17.1)

Adds ``works.queue_position`` so the reading queue can be reordered; NULL sorts last.

Revision ID: 0022_reading_queue_position
Revises: 0021_work_confirmed_fields
Create Date: 2026-06-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022_reading_queue_position"
down_revision: str | None = "0021_work_confirmed_fields"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    op.add_column("works", sa.Column("queue_position", sa.Integer(), nullable=True))
    op.create_index("ix_works_queue_position", "works", ["queue_position"])


def downgrade() -> None:
    """Revert the migration."""
    op.drop_index("ix_works_queue_position", table_name="works")
    op.drop_column("works", "queue_position")
