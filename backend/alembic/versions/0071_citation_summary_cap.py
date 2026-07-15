"""Citation-summary per-column item cap (UX batch): admin-editable, generous default — the UI
folds each column into a scrollable window instead of hiding the tail.

Revision ID: 0071_citation_summary_cap
Revises: 0070_matching_acceptance
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0071_citation_summary_cap"
down_revision: str | None = "0070_matching_acceptance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "app_config",
        sa.Column("citation_summary_item_cap", sa.Integer(), nullable=False, server_default="100"),
    )


def downgrade() -> None:
    op.drop_column("app_config", "citation_summary_item_cap")
