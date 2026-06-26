"""create summaries and topic_assignments tables

These two model tables (app/models/ai.py: Summary, TopicAssignment) shipped with the M7
summary/topic features but never had a migration — so a fully migrated Postgres was missing
them and the /works/{id}/summaries and /ai/topics endpoints would fail with UndefinedTable in
production. Tests did not catch this because they build the schema from Base.metadata directly.

Revision ID: 0010_summaries_topics
Revises: 0009_agents
Create Date: 2026-06-25 00:00:00.000000+00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_summaries_topics"
down_revision: str | None = "0009_agents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    op.create_table(
        "summaries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("entity_type", sa.String(length=64), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("summary_type", sa.String(length=64), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("model_name", sa.String(length=255), nullable=True),
        sa.Column("prompt_version", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_summaries_entity_id"), "summaries", ["entity_id"], unique=False)
    op.create_index(op.f("ix_summaries_entity_type"), "summaries", ["entity_type"], unique=False)
    op.create_index(op.f("ix_summaries_summary_type"), "summaries", ["summary_type"], unique=False)

    op.create_table(
        "topic_assignments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("topic_model_id", sa.String(length=255), nullable=False),
        sa.Column("scope_type", sa.String(length=64), nullable=False),
        sa.Column("scope_id", sa.String(length=255), nullable=True),
        sa.Column("work_id", sa.Uuid(), nullable=False),
        sa.Column("topic_id", sa.Integer(), nullable=False),
        sa.Column("score", sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_topic_assignments_scope_id"), "topic_assignments", ["scope_id"], unique=False
    )
    op.create_index(
        op.f("ix_topic_assignments_scope_type"), "topic_assignments", ["scope_type"], unique=False
    )
    op.create_index(
        op.f("ix_topic_assignments_topic_id"), "topic_assignments", ["topic_id"], unique=False
    )
    op.create_index(
        op.f("ix_topic_assignments_topic_model_id"),
        "topic_assignments",
        ["topic_model_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_topic_assignments_work_id"), "topic_assignments", ["work_id"], unique=False
    )


def downgrade() -> None:
    """Revert the migration."""
    op.drop_index(op.f("ix_topic_assignments_work_id"), table_name="topic_assignments")
    op.drop_index(op.f("ix_topic_assignments_topic_model_id"), table_name="topic_assignments")
    op.drop_index(op.f("ix_topic_assignments_topic_id"), table_name="topic_assignments")
    op.drop_index(op.f("ix_topic_assignments_scope_type"), table_name="topic_assignments")
    op.drop_index(op.f("ix_topic_assignments_scope_id"), table_name="topic_assignments")
    op.drop_table("topic_assignments")
    op.drop_index(op.f("ix_summaries_summary_type"), table_name="summaries")
    op.drop_index(op.f("ix_summaries_entity_type"), table_name="summaries")
    op.drop_index(op.f("ix_summaries_entity_id"), table_name="summaries")
    op.drop_table("summaries")
