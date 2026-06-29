"""Create agent_files (agent manifest entries + teleport state)

Revision ID: 0014_agent_files
Revises: 0013_citation_pdf_coordinates
Create Date: 2026-06-29

Changes
-------
* agent_files: one row per file an enrolled agent has indexed and reported via a manifest.
  Stores opaque identity (local_file_id, sha256, size) and a display-only path label — never a
  server-usable filesystem path — plus teleport state (none/requested/complete/failed) and the
  resulting managed File once teleported. Unique on (agent_id, local_file_id).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014_agent_files"
down_revision: str | None = "0013_citation_pdf_coordinates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    op.create_table(
        "agent_files",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "agent_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("local_file_id", sa.String(length=255), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("display_path", sa.String(length=1024), nullable=True),
        sa.Column("mime_type", sa.String(length=128), nullable=True),
        sa.Column("teleport_status", sa.String(length=32), nullable=False, server_default="none"),
        sa.Column(
            "file_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("files.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("requested_by_user_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("agent_id", "local_file_id", name="uq_agent_local_file"),
    )
    op.create_index("ix_agent_files_agent_id", "agent_files", ["agent_id"])
    op.create_index("ix_agent_files_local_file_id", "agent_files", ["local_file_id"])
    op.create_index("ix_agent_files_sha256", "agent_files", ["sha256"])
    op.create_index("ix_agent_files_teleport_status", "agent_files", ["teleport_status"])


def downgrade() -> None:
    """Revert the migration."""
    op.drop_index("ix_agent_files_teleport_status", table_name="agent_files")
    op.drop_index("ix_agent_files_sha256", table_name="agent_files")
    op.drop_index("ix_agent_files_local_file_id", table_name="agent_files")
    op.drop_index("ix_agent_files_agent_id", table_name="agent_files")
    op.drop_table("agent_files")
