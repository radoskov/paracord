"""Add agents.token_expires_at — optional agent-token expiry (AUDIT D3)

NULL keeps the pre-existing permanent-token behavior; an approval may now set an expiry so
short-lived tokens can be handed to temporary users. Additive + nullable (safe on live data).

Revision ID: 0068_agent_token_expiry
Revises: 0067_citing_cap_ai_threshold
Create Date: 2026-07-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0068_agent_token_expiry"
down_revision: str | None = "0067_citing_cap_ai_threshold"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    op.add_column(
        "agents", sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    """Revert the migration."""
    op.drop_column("agents", "token_expires_at")
