"""Add app_config.max_batch_items (D1 batch import cap)

Adds the owner-editable ceiling on how many items a single client import batch may carry. The server
default mirrors the ``_DEFAULT_MAX_BATCH_ITEMS`` constant in ``app.models.app_config``. Server-folder
scans are exempt from this cap and are not affected.

Revision ID: 0044_max_batch_items
Revises: 0043_rate_limit_config
Create Date: 2026-07-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0044_max_batch_items"
down_revision: str | None = "0043_rate_limit_config"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    op.add_column(
        "app_config",
        sa.Column("max_batch_items", sa.Integer(), nullable=False, server_default="100"),
    )


def downgrade() -> None:
    """Revert the migration."""
    op.drop_column("app_config", "max_batch_items")
