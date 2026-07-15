"""Elsevier API usage gating (UX batch 3): a global enable switch on app_config (NULL → enabled)
and a per-user allowance on users (NULL → NOT allowed).

Revision ID: 0073_elsevier_api_gating
Revises: 0072_elsevier_api_key
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0073_elsevier_api_gating"
down_revision: str | None = "0072_elsevier_api_key"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("app_config", sa.Column("elsevier_api_enabled", sa.Boolean(), nullable=True))
    op.add_column("users", sa.Column("elsevier_api_allowed", sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "elsevier_api_allowed")
    op.drop_column("app_config", "elsevier_api_enabled")
