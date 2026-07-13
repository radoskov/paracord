"""Add per-surface graph node caps to app_config (Insights audit L-a)

Owner decision 2026-07-13: high, easily-changeable caps per analysis surface. Above the cap a
graph keeps its highest-degree nodes and reports how many were hidden; scopes above the
background-job threshold compute on the worker instead of the request.

Revision ID: 0069_graph_node_caps
Revises: 0068_agent_token_expiry
Create Date: 2026-07-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0069_graph_node_caps"
down_revision: str | None = "0068_agent_token_expiry"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    op.add_column(
        "app_config",
        sa.Column("citation_graph_node_cap", sa.Integer(), nullable=False, server_default="1500"),
    )
    op.add_column(
        "app_config",
        sa.Column("topic_graph_node_cap", sa.Integer(), nullable=False, server_default="400"),
    )
    op.add_column(
        "app_config",
        sa.Column("viz_node_cap", sa.Integer(), nullable=False, server_default="500"),
    )


def downgrade() -> None:
    """Revert the migration."""
    op.drop_column("app_config", "viz_node_cap")
    op.drop_column("app_config", "topic_graph_node_cap")
    op.drop_column("app_config", "citation_graph_node_cap")
