"""Add app_config rate-limit ceilings (D1 overload protection)

Adds the owner-editable per-client and global request rate-limit ceilings (per rolling minute) to
the ``app_config`` settings singleton. Server defaults are read from the out-of-the-box constants in
``app.models.app_config`` so a freshly-inserted row keeps the built-in behaviour and the values are
defined in one place.

Revision ID: 0043_rate_limit_config
Revises: 0042_file_extraction_owed
Create Date: 2026-07-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from app.models.app_config import (
    _DEFAULT_RATE_LIMIT_GLOBAL_PER_MIN,
    _DEFAULT_RATE_LIMIT_PER_CLIENT_PER_MIN,
)

revision: str = "0043_rate_limit_config"
down_revision: str | None = "0042_file_extraction_owed"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    op.add_column(
        "app_config",
        sa.Column(
            "rate_limit_per_client_per_min",
            sa.Integer(),
            nullable=False,
            server_default=str(_DEFAULT_RATE_LIMIT_PER_CLIENT_PER_MIN),
        ),
    )
    op.add_column(
        "app_config",
        sa.Column(
            "rate_limit_global_per_min",
            sa.Integer(),
            nullable=False,
            server_default=str(_DEFAULT_RATE_LIMIT_GLOBAL_PER_MIN),
        ),
    )


def downgrade() -> None:
    """Revert the migration."""
    op.drop_column("app_config", "rate_limit_global_per_min")
    op.drop_column("app_config", "rate_limit_per_client_per_min")
