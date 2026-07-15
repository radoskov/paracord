"""Matching acceptance settings (UX batch): admin-editable fuzzy auto-accept threshold + the
high-confidence auto-accept toggle on the app-config singleton.

Both nullable, no server default: NULL means "use the built-in/yaml default" (threshold → the
``reference_matching.auto_accept_threshold`` yaml value; the high-confidence toggle → ON).

Revision ID: 0070_matching_acceptance
Revises: 0069_graph_node_caps
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0070_matching_acceptance"
down_revision: str | None = "0069_graph_node_caps"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("app_config", sa.Column("fuzzy_accept_threshold", sa.Float(), nullable=True))
    op.add_column(
        "app_config", sa.Column("use_high_confidence_auto_accept", sa.Boolean(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("app_config", "use_high_confidence_auto_accept")
    op.drop_column("app_config", "fuzzy_accept_threshold")
