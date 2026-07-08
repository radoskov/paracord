"""Drop the dead ``agents.revoked_at`` column (AUDIT L7 cleanup)

``Agent.revoked_at`` was never read or written: revocation is enforced by ``status != "approved"``
and ``delete_agent`` (which removes the row and its token), both of which correctly 401 a stale
agent token. The column was pure dead weight (added in 0020) and is removed here.

Revision ID: 0055_drop_agent_revoked_at
Revises: 0054_agentfile_work_id
Create Date: 2026-07-09
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0055_drop_agent_revoked_at"
down_revision: str | None = "0054_agentfile_work_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    op.drop_column("agents", "revoked_at")


def downgrade() -> None:
    """Revert the migration."""
    op.add_column(
        "agents",
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
