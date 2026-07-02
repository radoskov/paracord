"""Add user.papers_per_page and the app_config singleton (D18 library pagination)

Adds a nullable per-user ``papers_per_page`` preference (NULL falls back to the server default) and
an ``app_config`` settings singleton holding the owner-editable global ``max_papers_per_page`` clamp
(server default 500, mirroring ``Settings.max_papers_per_page``).

Revision ID: 0041_papers_per_page
Revises: 0040_ai_config_ocr_language
Create Date: 2026-07-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0041_papers_per_page"
down_revision: str | None = "0040_ai_config_ocr_language"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    op.add_column("users", sa.Column("papers_per_page", sa.Integer(), nullable=True))
    op.create_table(
        "app_config",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("max_papers_per_page", sa.Integer(), nullable=False, server_default="500"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by_user_id", sa.Uuid(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    """Revert the migration."""
    op.drop_table("app_config")
    op.drop_column("users", "papers_per_page")
