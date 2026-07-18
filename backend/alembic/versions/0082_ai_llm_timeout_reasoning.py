"""Add ai_config summary LLM timeout + opt-in reasoning-mode controls.

``summary_llm_timeout`` is the per-call timeout (seconds) for a local-LLM summary; ``summary_reasoning``
opts a reasoning model into actually thinking before answering (slower, higher quality). Both NULL →
their Settings defaults.

Revision ID: 0082_ai_llm_timeout_reasoning
Revises: 0081_ai_config_cache_autounmount
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0082_ai_llm_timeout_reasoning"
down_revision: str | None = "0081_ai_config_cache_autounmount"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("ai_config", sa.Column("summary_llm_timeout", sa.Float(), nullable=True))
    op.add_column("ai_config", sa.Column("summary_reasoning", sa.Boolean(), nullable=True))


def downgrade() -> None:
    op.drop_column("ai_config", "summary_reasoning")
    op.drop_column("ai_config", "summary_llm_timeout")
