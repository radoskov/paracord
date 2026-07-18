"""Add ai_config.vram_budget_gb — admin-set VRAM/RAM budget (GB) for the Ollama host, used by the
model mount/unmount panel to warn before a load would exceed available memory.

Revision ID: 0080_ai_config_vram_budget
Revises: 0079_recommendation_runs
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0080_ai_config_vram_budget"
down_revision: str | None = "0079_recommendation_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("ai_config", sa.Column("vram_budget_gb", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("ai_config", "vram_budget_gb")
