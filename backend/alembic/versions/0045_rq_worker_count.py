"""Add app_config.rq_worker_count (D1 worker supervisor)

Adds the owner-editable number of RQ extraction worker processes the supervisor launches at worker
container start. The server default mirrors ``_DEFAULT_RQ_WORKER_COUNT`` in
``app.models.app_config``. Applied on worker restart (the supervisor reads it once at startup).

Revision ID: 0045_rq_worker_count
Revises: 0044_max_batch_items
Create Date: 2026-07-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0045_rq_worker_count"
down_revision: str | None = "0044_max_batch_items"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    op.add_column(
        "app_config",
        sa.Column("rq_worker_count", sa.Integer(), nullable=False, server_default="2"),
    )


def downgrade() -> None:
    """Revert the migration."""
    op.drop_column("app_config", "rq_worker_count")
