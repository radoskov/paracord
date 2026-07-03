"""Add the custom_themes table (Theming P4, runtime admin-managed themes)

Creates ``custom_themes``: an owner/admin-uploaded GUI theme authored as YAML (``yaml_source``),
with ``slug``/``name``/``mode``/``temperature`` denormalised out of it for the picker list. ``slug``
is unique (the ``data-theme`` id, never equal to a bundled theme id — enforced in the service).
``created_by`` FKs ``users.id`` with ON DELETE SET NULL so a theme survives its author's removal.
Storing the YAML in the DB keeps custom themes inside the normal database backup.

Revision ID: 0051_custom_themes
Revises: 0050_user_theme
Create Date: 2026-07-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0051_custom_themes"
down_revision: str | None = "0050_user_theme"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    op.create_table(
        "custom_themes",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("temperature", sa.String(length=16), nullable=False),
        sa.Column("yaml_source", sa.Text(), nullable=False),
        sa.Column(
            "created_by",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        op.f("ix_custom_themes_slug"),
        "custom_themes",
        ["slug"],
        unique=True,
    )


def downgrade() -> None:
    """Revert the migration."""
    op.drop_index(op.f("ix_custom_themes_slug"), table_name="custom_themes")
    op.drop_table("custom_themes")
