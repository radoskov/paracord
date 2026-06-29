"""Extend agent_files for import actions, teleport policy/block, virtual path, preview (SPEC §32)

Revision ID: 0016_agent_file_actions
Revises: 0015_agent_privileges
Create Date: 2026-06-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016_agent_file_actions"
down_revision: str | None = "0015_agent_privileges"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    op.add_column("agent_files", sa.Column("virtual_path", sa.String(length=1024), nullable=True))
    op.add_column(
        "agent_files",
        sa.Column(
            "import_action", sa.String(length=32), nullable=False, server_default="index_only"
        ),
    )
    op.add_column(
        "agent_files",
        sa.Column("teleport_policy", sa.String(length=16), nullable=False, server_default="ask"),
    )
    op.add_column(
        "agent_files",
        sa.Column(
            "processing_state", sa.String(length=32), nullable=False, server_default="indexed"
        ),
    )
    op.add_column(
        "agent_files",
        sa.Column("teleport_blocked", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column("agent_files", sa.Column("preview_text", sa.Text(), nullable=True))
    op.create_index("ix_agent_files_processing_state", "agent_files", ["processing_state"])


def downgrade() -> None:
    """Revert the migration."""
    op.drop_index("ix_agent_files_processing_state", table_name="agent_files")
    for column in (
        "preview_text",
        "teleport_blocked",
        "processing_state",
        "teleport_policy",
        "import_action",
        "virtual_path",
    ):
        op.drop_column("agent_files", column)
