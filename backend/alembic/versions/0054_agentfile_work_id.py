"""Link an agent file to the stub work it created (B6 / issue_batch_6 #4)

``agent_files.work_id`` ties an ``index_only`` manifest entry to the minimal library "stub" paper it
creates, so a re-scan never duplicates the stub and a later extract/teleport enriches that same work.
FK ``ON DELETE SET NULL`` so deleting the paper just detaches the agent file.

Revision ID: 0054_agentfile_work_id
Revises: 0053_work_merge_shadow
Create Date: 2026-07-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0054_agentfile_work_id"
down_revision: str | None = "0053_work_merge_shadow"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    op.add_column("agent_files", sa.Column("work_id", sa.Uuid(as_uuid=True), nullable=True))
    op.create_index(op.f("ix_agent_files_work_id"), "agent_files", ["work_id"])
    op.create_foreign_key(
        "fk_agent_files_work_id",
        "agent_files",
        "works",
        ["work_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    """Revert the migration."""
    op.drop_constraint("fk_agent_files_work_id", "agent_files", type_="foreignkey")
    op.drop_index(op.f("ix_agent_files_work_id"), table_name="agent_files")
    op.drop_column("agent_files", "work_id")
