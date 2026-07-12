"""Add app_config.use_fuzzy_match_as_confirmed (batch 12, owner item #1)

Owner-editable runtime toggle. When ON, a fuzzy "likely local" reference match that clears the title
threshold + gates becomes a hard link (``resolved_work_id`` set, counted in every graph/metric
calculation) instead of a soft one-click suggestion. Nullable with no server default: an absent row
or a NULL value reproduces the OFF default (matching ``app.services.app_config`` null-guards).

Revision ID: 0060_fuzzy_match_as_confirmed
Revises: 0059_canonical_references
Create Date: 2026-07-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0060_fuzzy_match_as_confirmed"
down_revision: str | None = "0059_canonical_references"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    op.add_column(
        "app_config",
        sa.Column("use_fuzzy_match_as_confirmed", sa.Boolean(), nullable=True),
    )


def downgrade() -> None:
    """Revert the migration."""
    op.drop_column("app_config", "use_fuzzy_match_as_confirmed")
