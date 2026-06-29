"""Add per-agent privilege columns (SPEC §32.8)

Revision ID: 0015_agent_privileges
Revises: 0014_agent_files
Create Date: 2026-06-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015_agent_privileges"
down_revision: str | None = "0014_agent_files"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS = (
    ("can_index", "true"),
    ("can_extract", "true"),
    ("can_teleport", "false"),
    ("can_be_requested", "true"),
    ("processing_visibility", "true"),
    ("server_status_visibility", "true"),
)


def upgrade() -> None:
    """Apply the migration."""
    for name, default in _COLUMNS:
        op.add_column(
            "agents", sa.Column(name, sa.Boolean(), nullable=False, server_default=default)
        )


def downgrade() -> None:
    """Revert the migration."""
    for name, _default in reversed(_COLUMNS):
        op.drop_column("agents", name)
