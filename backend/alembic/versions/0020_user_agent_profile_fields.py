"""User + agent profile/lifecycle fields (SPEC §9.3; AUDIT §3)

Adds user profile/account metadata (display_name, email, last_login_at, password_changed_at) and
agent identity/lifecycle metadata (host_alias, capabilities JSONB, last_seen_at, created_by_user_id,
revoked_at). All nullable/additive.

Revision ID: 0020_user_agent_profile_fields
Revises: 0019_pgvector
Create Date: 2026-06-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0020_user_agent_profile_fields"
down_revision: str | None = "0019_pgvector"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSONB = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    """Apply the migration."""
    op.add_column("users", sa.Column("display_name", sa.String(length=255), nullable=True))
    op.add_column("users", sa.Column("email", sa.String(length=320), nullable=True))
    op.add_column("users", sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "users", sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("agents", sa.Column("host_alias", sa.String(length=255), nullable=True))
    op.add_column("agents", sa.Column("capabilities", _JSONB, nullable=True))
    op.add_column("agents", sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("agents", sa.Column("created_by_user_id", sa.Uuid(as_uuid=True), nullable=True))
    op.add_column("agents", sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    """Revert the migration."""
    for col in ("revoked_at", "created_by_user_id", "last_seen_at", "capabilities", "host_alias"):
        op.drop_column("agents", col)
    for col in ("password_changed_at", "last_login_at", "email", "display_name"):
        op.drop_column("users", col)
