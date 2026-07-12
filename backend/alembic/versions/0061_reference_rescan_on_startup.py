"""Add app_config.reference_rescan_on_startup (F3a — reference-resolution caching repair)

Owner-editable runtime toggle. When ON, the API enqueues a full library-wide reference→work rematch
on startup (best-effort, like the D7 owed-extraction sweep) so the stored reference resolution stays
fresh across deploys. Nullable with no server default: an absent row or a NULL value reproduces the
OFF default (matching ``app.services.app_config`` null-guards).

Revision ID: 0061_reference_rescan_on_startup
Revises: 0060_fuzzy_match_as_confirmed
Create Date: 2026-07-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0061_reference_rescan_on_startup"
down_revision: str | None = "0060_fuzzy_match_as_confirmed"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    op.add_column(
        "app_config",
        sa.Column("reference_rescan_on_startup", sa.Boolean(), nullable=True),
    )


def downgrade() -> None:
    """Revert the migration."""
    op.drop_column("app_config", "reference_rescan_on_startup")
