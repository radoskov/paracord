"""Indexes on shelf_works.work_id and rack_shelves.shelf_id (audit: efficiency #4).

These join/filter columns are the trailing member of their composite PKs, so the PK index can't
serve the work_id-/shelf_id-leading lookups used on nearly every access-control check and
visible-works filter. Standalone indexes back those hot paths.

Revision ID: 0039_access_indexes
Revises: 0038_work_main_file
Create Date: 2026-07-02
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0039_access_indexes"
down_revision: str | None = "0038_work_main_file"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("ix_shelf_works_work_id", "shelf_works", ["work_id"])
    op.create_index("ix_rack_shelves_shelf_id", "rack_shelves", ["shelf_id"])


def downgrade() -> None:
    op.drop_index("ix_rack_shelves_shelf_id", table_name="rack_shelves")
    op.drop_index("ix_shelf_works_work_id", table_name="shelf_works")
