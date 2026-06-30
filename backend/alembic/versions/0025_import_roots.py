"""GUI-managed server import roots (batch 2 #19)

Adds the ``import_roots`` table: an owner-managed whitelist of server-local folders for the
"Server folder" import, stored in the DB and **merged** with the read-only
``storage.server_allowed_roots`` entries from ``server.yaml`` (the YAML is never written to). Each
row pins an absolute ``path`` to a unique ``alias``.

Revision ID: 0025_import_roots
Revises: 0024_role_redesign
Create Date: 2026-06-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025_import_roots"
down_revision: str | None = "0024_role_redesign"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    op.create_table(
        "import_roots",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("alias", sa.String(length=255), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    # ``alias`` is unique=index=True on the model -> a single unique index (no separate constraint).
    op.create_index("ix_import_roots_alias", "import_roots", ["alias"], unique=True)
    op.create_index(
        "ix_import_roots_created_by_user_id", "import_roots", ["created_by_user_id"], unique=False
    )


def downgrade() -> None:
    """Revert the migration."""
    op.drop_index("ix_import_roots_created_by_user_id", table_name="import_roots")
    op.drop_index("ix_import_roots_alias", table_name="import_roots")
    op.drop_table("import_roots")
