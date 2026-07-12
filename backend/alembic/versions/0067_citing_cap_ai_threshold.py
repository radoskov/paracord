"""Add app_config.citing_papers_fetch_cap + ai_scope_job_threshold (S20 / S15-S16)

Two owner-editable runtime knobs on the app_config singleton:

* ``citing_papers_fetch_cap`` (default 1000) — how many citing papers one fetch pages from the
  provider and caches per paper (was a hardcoded 100; papers with more citers stored a sample
  invisible to the local matcher).
* ``ai_scope_job_threshold`` (default 100) — scopes larger than this many papers run topic-model
  and summary requests on the background worker instead of inline in the request.

Server defaults keep existing rows valid (additive, safe on live data).

Revision ID: 0067_citing_cap_ai_threshold
Revises: 0066_citing_fetch_snapshot
Create Date: 2026-07-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0067_citing_cap_ai_threshold"
down_revision: str | None = "0066_citing_fetch_snapshot"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    op.add_column(
        "app_config",
        sa.Column("citing_papers_fetch_cap", sa.Integer(), nullable=False, server_default="1000"),
    )
    op.add_column(
        "app_config",
        sa.Column("ai_scope_job_threshold", sa.Integer(), nullable=False, server_default="100"),
    )


def downgrade() -> None:
    """Revert the migration."""
    op.drop_column("app_config", "ai_scope_job_threshold")
    op.drop_column("app_config", "citing_papers_fetch_cap")
