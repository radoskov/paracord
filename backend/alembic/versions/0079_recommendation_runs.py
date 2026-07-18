"""Cached AI recommendation runs (feature: Insights → Recommend categorization). One table caching a
recommendation run per (scope + settings + model) so a dropped connection / refresh doesn't force a
full recompute.

Revision ID: 0079_recommendation_runs
Revises: 0078_rows
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0079_recommendation_runs"
down_revision: str | None = "0078_rows"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "recommendation_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("scope_type", sa.String(length=32), nullable=False),
        sa.Column("scope_id", sa.Uuid(), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("params_hash", sa.String(length=64), nullable=False),
        sa.Column("params", sa.JSON(), nullable=True),
        sa.Column("model_name", sa.String(length=255), nullable=True),
        sa.Column("provider_used", sa.String(length=64), nullable=True),
        sa.Column("fallback", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("error", sa.String(length=1024), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_recommendation_runs_scope_type"), "recommendation_runs", ["scope_type"]
    )
    op.create_index(op.f("ix_recommendation_runs_scope_id"), "recommendation_runs", ["scope_id"])
    op.create_index(op.f("ix_recommendation_runs_mode"), "recommendation_runs", ["mode"])
    op.create_index(
        op.f("ix_recommendation_runs_params_hash"), "recommendation_runs", ["params_hash"]
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_recommendation_runs_params_hash"), table_name="recommendation_runs")
    op.drop_index(op.f("ix_recommendation_runs_mode"), table_name="recommendation_runs")
    op.drop_index(op.f("ix_recommendation_runs_scope_id"), table_name="recommendation_runs")
    op.drop_index(op.f("ix_recommendation_runs_scope_type"), table_name="recommendation_runs")
    op.drop_table("recommendation_runs")
