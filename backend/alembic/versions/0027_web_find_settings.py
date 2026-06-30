"""Owner-managed global find-on-web settings (find-on-web v2 download-policy modes)

Adds the single-row ``web_find_settings`` table holding the global find-on-web download-policy
mode (``restricted`` / ``careful`` / ``unrestricted``). An absent row means the conservative
``restricted`` default. Owner-only, edited from the Admin UI.

Revision ID: 0027_web_find_settings
Revises: 0026_web_find_allowed_hosts
Create Date: 2026-06-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0027_web_find_settings"
down_revision: str | None = "0026_web_find_allowed_hosts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    op.create_table(
        "web_find_settings",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("download_policy", sa.String(length=32), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by_user_id", sa.Uuid(as_uuid=True), nullable=True),
    )


def downgrade() -> None:
    """Revert the migration."""
    op.drop_table("web_find_settings")
