"""Detailed-summary effort levels (2026-07-16): the single detailed level became three
(fast/section/deep). Migrate existing per-paper/scope detailed rows to the 'deep' level so the
cache matrix finds them (the old '_detailed' suffix becomes '_detailed_deep').

Data-only migration — no schema change.

Revision ID: 0074_summary_effort_levels
Revises: 0073_elsevier_api_gating
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0074_summary_effort_levels"
down_revision: str | None = "0073_elsevier_api_gating"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # e.g. 'local_llm_detailed' -> 'local_llm_detailed_deep'. Guard against re-running: only rows
    # ending in '_detailed' (not already '_detailed_deep'/'_fast'/'_section').
    op.execute(
        "UPDATE summaries SET summary_type = summary_type || '_deep' "
        "WHERE summary_type LIKE '%\\_detailed' ESCAPE '\\'"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE summaries SET summary_type = left(summary_type, -5) "
        "WHERE summary_type LIKE '%\\_detailed\\_deep' ESCAPE '\\'"
    )
