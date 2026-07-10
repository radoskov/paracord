"""Add import staging tables (batch 10, issue 1: multi-PDF import with extraction preview)

Two tables back the "extract before storing records" flow: ``import_staging_batches`` (one
multi-PDF session) and ``import_staging_items`` (one staged PDF, its content-addressed file,
extraction outcome, and detected collisions). Records live only until commit mints real Works.

Revision ID: 0056_import_staging
Revises: 0055_drop_agent_revoked_at
Create Date: 2026-07-10
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0056_import_staging"
down_revision: str | None = "0055_drop_agent_revoked_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSONB = sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    """Apply the migration."""
    op.create_table(
        "import_staging_batches",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("target_shelf_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_import_staging_batches_created_by_user_id"),
        "import_staging_batches",
        ["created_by_user_id"],
    )
    op.create_index(op.f("ix_import_staging_batches_status"), "import_staging_batches", ["status"])

    op.create_table(
        "import_staging_items",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("batch_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("file_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("parsed", _JSONB, nullable=True),
        sa.Column("tei_xml", sa.Text(), nullable=True),
        sa.Column("duplicates", _JSONB, nullable=True),
        sa.Column("created_work_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["batch_id"], ["import_staging_batches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["file_id"], ["files.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_import_staging_items_batch_id"), "import_staging_items", ["batch_id"])
    op.create_index(op.f("ix_import_staging_items_file_id"), "import_staging_items", ["file_id"])
    op.create_index(op.f("ix_import_staging_items_sha256"), "import_staging_items", ["sha256"])
    op.create_index(op.f("ix_import_staging_items_status"), "import_staging_items", ["status"])


def downgrade() -> None:
    """Revert the migration."""
    op.drop_index(op.f("ix_import_staging_items_status"), table_name="import_staging_items")
    op.drop_index(op.f("ix_import_staging_items_sha256"), table_name="import_staging_items")
    op.drop_index(op.f("ix_import_staging_items_file_id"), table_name="import_staging_items")
    op.drop_index(op.f("ix_import_staging_items_batch_id"), table_name="import_staging_items")
    op.drop_table("import_staging_items")
    op.drop_index(op.f("ix_import_staging_batches_status"), table_name="import_staging_batches")
    op.drop_index(
        op.f("ix_import_staging_batches_created_by_user_id"),
        table_name="import_staging_batches",
    )
    op.drop_table("import_staging_batches")
