"""Add ai_config query-embedding cache size + auto-unmount controls.

``query_cache_size`` bounds the per-model query-embedding LRU cache (NULL → Settings default);
``auto_unmount`` / ``auto_unmount_minutes`` drive the ``keep_alive`` applied to on-demand Ollama
use so admins can control how long summary/embedding models linger in VRAM.

Revision ID: 0081_ai_config_cache_autounmount
Revises: 0080_ai_config_vram_budget
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0081_ai_config_cache_autounmount"
down_revision: str | None = "0080_ai_config_vram_budget"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("ai_config", sa.Column("query_cache_size", sa.Integer(), nullable=True))
    op.add_column("ai_config", sa.Column("auto_unmount", sa.Boolean(), nullable=True))
    op.add_column("ai_config", sa.Column("auto_unmount_minutes", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("ai_config", "auto_unmount_minutes")
    op.drop_column("ai_config", "auto_unmount")
    op.drop_column("ai_config", "query_cache_size")
