"""create reader annotations

Revision ID: 0007_annotations
Revises: 0006_dupe_candidates
Create Date: 2026-06-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_annotations"
down_revision: str | None = "0006_dupe_candidates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    op.create_table(
        "annotations",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("work_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("file_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("version_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("coordinates", sa.JSON(), nullable=True),
        sa.Column("selected_text", sa.Text(), nullable=True),
        sa.Column("annotation_type", sa.String(length=64), nullable=False),
        sa.Column("content_markdown", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_annotations_work_id", "annotations", ["work_id"])
    op.create_index("ix_annotations_file_id", "annotations", ["file_id"])
    op.create_index("ix_annotations_version_id", "annotations", ["version_id"])
    op.create_index("ix_annotations_page", "annotations", ["page"])
    op.create_index("ix_annotations_annotation_type", "annotations", ["annotation_type"])
    op.create_index("ix_annotations_created_by_user_id", "annotations", ["created_by_user_id"])


def downgrade() -> None:
    """Revert the migration."""
    op.drop_index("ix_annotations_created_by_user_id", table_name="annotations")
    op.drop_index("ix_annotations_annotation_type", table_name="annotations")
    op.drop_index("ix_annotations_page", table_name="annotations")
    op.drop_index("ix_annotations_version_id", table_name="annotations")
    op.drop_index("ix_annotations_file_id", table_name="annotations")
    op.drop_index("ix_annotations_work_id", table_name="annotations")
    op.drop_table("annotations")
