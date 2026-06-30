"""Access-control foundation schema (Phase H, items 1-3)

DDL for the user-group + rack/shelf ACL foundation:

- ``groups`` / ``group_memberships`` / ``group_grants`` / ``default_grants`` — Linux-like user
  groups and their grants to rack/shelf targets, plus the default-grant set for new personal groups.
- ``access_settings`` — single-row global default-access-level setting (mirrors web_find_settings).
- ``racks.access_level`` / ``shelves.access_level`` — ``open`` / ``visible`` / ``private`` (added
  with a server_default of ``'open'`` for existing rows, then the server default is dropped so the
  ORM supplies it on insert).
- ``works.created_by_user_id`` — nullable owner column (NULL = system/agent/import "loose" paper).

The companion ``0029_access_control_backfill`` then creates a personal group for every existing
user. The ``role`` column stays VARCHAR (no Postgres enum), so the new ``contributor``/``librarian``
roles need no schema change.

Revision ID: 0028_access_control_schema
Revises: 0027_web_find_settings
Create Date: 2026-07-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0028_access_control_schema"
down_revision: str | None = "0027_web_find_settings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    # --- groups ---------------------------------------------------------------------------------
    op.create_table(
        "groups",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("is_personal", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "personal_user_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    # ``name`` is unique=index=True on the model -> a single unique index (no separate constraint).
    op.create_index("ix_groups_name", "groups", ["name"], unique=True)
    op.create_index("ix_groups_personal_user_id", "groups", ["personal_user_id"], unique=False)
    # Drop the server-side default now existing rows (none) are covered; the ORM supplies it.
    op.alter_column("groups", "is_personal", server_default=None)

    # --- group_memberships ----------------------------------------------------------------------
    op.create_table(
        "group_memberships",
        sa.Column(
            "group_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("groups.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("added_by_user_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_group_memberships_user_id", "group_memberships", ["user_id"], unique=False)

    # --- group_grants ---------------------------------------------------------------------------
    op.create_table(
        "group_grants",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "group_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("groups.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("target_type", sa.String(length=16), nullable=False),
        sa.Column("target_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("added_by_user_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("group_id", "target_type", "target_id", name="uq_group_grant_target"),
    )
    op.create_index("ix_group_grants_group_id", "group_grants", ["group_id"], unique=False)

    # --- default_grants -------------------------------------------------------------------------
    op.create_table(
        "default_grants",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("target_type", sa.String(length=16), nullable=False),
        sa.Column("target_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("added_by_user_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("target_type", "target_id", name="uq_default_grant_target"),
    )

    # --- access_settings (single-row singleton) -------------------------------------------------
    op.create_table(
        "access_settings",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("default_access_level", sa.String(length=16), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by_user_id", sa.Uuid(as_uuid=True), nullable=True),
    )

    # --- rack/shelf access_level ----------------------------------------------------------------
    # Add NOT NULL with a server_default so existing rows get 'open', then drop the server default
    # (the ORM supplies the default on insert; residual server_default is tolerated by parity).
    op.add_column(
        "racks",
        sa.Column("access_level", sa.String(length=16), nullable=False, server_default="open"),
    )
    op.alter_column("racks", "access_level", server_default=None)
    op.add_column(
        "shelves",
        sa.Column("access_level", sa.String(length=16), nullable=False, server_default="open"),
    )
    op.alter_column("shelves", "access_level", server_default=None)

    # --- works.created_by_user_id ---------------------------------------------------------------
    op.add_column(
        "works",
        sa.Column("created_by_user_id", sa.Uuid(as_uuid=True), nullable=True),
    )
    op.create_index("ix_works_created_by_user_id", "works", ["created_by_user_id"], unique=False)


def downgrade() -> None:
    """Revert the migration."""
    op.drop_index("ix_works_created_by_user_id", table_name="works")
    op.drop_column("works", "created_by_user_id")
    op.drop_column("shelves", "access_level")
    op.drop_column("racks", "access_level")
    op.drop_table("access_settings")
    op.drop_table("default_grants")
    op.drop_index("ix_group_grants_group_id", table_name="group_grants")
    op.drop_table("group_grants")
    op.drop_index("ix_group_memberships_user_id", table_name="group_memberships")
    op.drop_table("group_memberships")
    op.drop_index("ix_groups_personal_user_id", table_name="groups")
    op.drop_index("ix_groups_name", table_name="groups")
    op.drop_table("groups")
