"""create agents and agent_enrollment_tokens tables

Revision ID: 0009_agents
Revises: 0008_embeddings
Create Date: 2026-06-25 00:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009_agents"
down_revision: str | None = "0008_embeddings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    op.create_table(
        "agents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("token_hash", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by_user_id", sa.Uuid(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_agents_name"), "agents", ["name"], unique=False)
    op.create_index(op.f("ix_agents_status"), "agents", ["status"], unique=False)
    op.create_index(op.f("ix_agents_token_hash"), "agents", ["token_hash"], unique=False)
    op.create_index(
        op.f("ix_agents_approved_by_user_id"), "agents", ["approved_by_user_id"], unique=False
    )

    op.create_table(
        "agent_enrollment_tokens",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(length=255), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_by_agent_id", sa.Uuid(), nullable=True),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_agent_enrollment_token_hash"),
    )
    op.create_index(
        op.f("ix_agent_enrollment_tokens_token_hash"),
        "agent_enrollment_tokens",
        ["token_hash"],
        unique=False,
    )
    op.create_index(
        op.f("ix_agent_enrollment_tokens_created_by_user_id"),
        "agent_enrollment_tokens",
        ["created_by_user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_agent_enrollment_tokens_used_by_agent_id"),
        "agent_enrollment_tokens",
        ["used_by_agent_id"],
        unique=False,
    )


def downgrade() -> None:
    """Revert the migration."""
    op.drop_index(
        op.f("ix_agent_enrollment_tokens_used_by_agent_id"), table_name="agent_enrollment_tokens"
    )
    op.drop_index(
        op.f("ix_agent_enrollment_tokens_created_by_user_id"), table_name="agent_enrollment_tokens"
    )
    op.drop_index(
        op.f("ix_agent_enrollment_tokens_token_hash"), table_name="agent_enrollment_tokens"
    )
    op.drop_table("agent_enrollment_tokens")
    op.drop_index(op.f("ix_agents_approved_by_user_id"), table_name="agents")
    op.drop_index(op.f("ix_agents_token_hash"), table_name="agents")
    op.drop_index(op.f("ix_agents_status"), table_name="agents")
    op.drop_index(op.f("ix_agents_name"), table_name="agents")
    op.drop_table("agents")
