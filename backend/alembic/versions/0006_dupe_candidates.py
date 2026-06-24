"""create duplicate candidate review queue

Revision ID: 0006_dupe_candidates
Revises: 0005_raw_tei_mentions
Create Date: 2026-06-24
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_dupe_candidates"
down_revision: str | None = "0005_raw_tei_mentions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    op.create_table(
        "duplicate_candidates",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("candidate_type", sa.String(length=64), nullable=False),
        sa.Column("entity_a_type", sa.String(length=64), nullable=False),
        sa.Column("entity_a_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("entity_b_type", sa.String(length=64), nullable=False),
        sa.Column("entity_b_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("signals", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("resolved_by_user_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint(
            "candidate_type",
            "entity_a_type",
            "entity_a_id",
            "entity_b_type",
            "entity_b_id",
            name="uq_duplicate_candidate_pair",
        ),
    )
    op.create_index(
        "ix_duplicate_candidates_candidate_type",
        "duplicate_candidates",
        ["candidate_type"],
    )
    op.create_index(
        "ix_duplicate_candidates_entity_a_type",
        "duplicate_candidates",
        ["entity_a_type"],
    )
    op.create_index(
        "ix_duplicate_candidates_entity_a_id",
        "duplicate_candidates",
        ["entity_a_id"],
    )
    op.create_index(
        "ix_duplicate_candidates_entity_b_type",
        "duplicate_candidates",
        ["entity_b_type"],
    )
    op.create_index(
        "ix_duplicate_candidates_entity_b_id",
        "duplicate_candidates",
        ["entity_b_id"],
    )
    op.create_index("ix_duplicate_candidates_status", "duplicate_candidates", ["status"])
    op.create_index(
        "ix_duplicate_candidates_resolved_by_user_id",
        "duplicate_candidates",
        ["resolved_by_user_id"],
    )


def downgrade() -> None:
    """Revert the migration."""
    op.drop_index("ix_duplicate_candidates_resolved_by_user_id", table_name="duplicate_candidates")
    op.drop_index("ix_duplicate_candidates_status", table_name="duplicate_candidates")
    op.drop_index("ix_duplicate_candidates_entity_b_id", table_name="duplicate_candidates")
    op.drop_index("ix_duplicate_candidates_entity_b_type", table_name="duplicate_candidates")
    op.drop_index("ix_duplicate_candidates_entity_a_id", table_name="duplicate_candidates")
    op.drop_index("ix_duplicate_candidates_entity_a_type", table_name="duplicate_candidates")
    op.drop_index("ix_duplicate_candidates_candidate_type", table_name="duplicate_candidates")
    op.drop_table("duplicate_candidates")
