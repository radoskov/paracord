"""Add app_config.max_queue_len (D39 queue-length cap)

Adds the owner-editable ceiling on the pending RQ queue depth. A job-creating request is rejected
with 429 once the pending queue is already at this cap; the measurement fails open (allows) when
Redis is unreachable. The server default mirrors ``_DEFAULT_MAX_QUEUE_LEN`` in
``app.models.app_config``.

Revision ID: 0046_max_queue_len
Revises: 0045_rq_worker_count
Create Date: 2026-07-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0046_max_queue_len"
down_revision: str | None = "0045_rq_worker_count"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    op.add_column(
        "app_config",
        sa.Column("max_queue_len", sa.Integer(), nullable=False, server_default="1000"),
    )


def downgrade() -> None:
    """Revert the migration."""
    op.drop_column("app_config", "max_queue_len")
