"""GUI-managed find-on-web allowed download hosts (batch 2 #5 hardening)

Adds the ``web_find_allowed_hosts`` table: an owner/admin-managed allowlist of additional hosts
that find-on-web may download a PDF from, stored in the DB and **merged** with the built-in
default allowlist in ``app.services.web_find`` (the defaults are never written to). Each row pins
one unique ``host`` pattern.

Revision ID: 0026_web_find_allowed_hosts
Revises: 0025_import_roots
Create Date: 2026-06-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0026_web_find_allowed_hosts"
down_revision: str | None = "0025_import_roots"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    op.create_table(
        "web_find_allowed_hosts",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("host", sa.String(length=255), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    # ``host`` is unique=index=True on the model -> a single unique index (no separate constraint).
    op.create_index(
        "ix_web_find_allowed_hosts_host", "web_find_allowed_hosts", ["host"], unique=True
    )
    op.create_index(
        "ix_web_find_allowed_hosts_created_by_user_id",
        "web_find_allowed_hosts",
        ["created_by_user_id"],
        unique=False,
    )


def downgrade() -> None:
    """Revert the migration."""
    op.drop_index(
        "ix_web_find_allowed_hosts_created_by_user_id", table_name="web_find_allowed_hosts"
    )
    op.drop_index("ix_web_find_allowed_hosts_host", table_name="web_find_allowed_hosts")
    op.drop_table("web_find_allowed_hosts")
