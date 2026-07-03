"""Add per-user GUI theme preference (Theming P3)

Persist the signed-in user's chosen GUI theme id (``latte-warm``/``latte-cool``/``mocha-warm``/
``mocha-cool``). NULL means "use the boot default"; the API validates any set value against the
bundled theme ids. Mirrors the existing ``papers_per_page`` per-user preference pattern.

Revision ID: 0050_user_theme
Revises: 0049_work_citation_count
Create Date: 2026-07-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0050_user_theme"
down_revision: str | None = "0049_work_citation_count"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    op.add_column("users", sa.Column("theme", sa.String(length=32), nullable=True))


def downgrade() -> None:
    """Revert the migration."""
    op.drop_column("users", "theme")
