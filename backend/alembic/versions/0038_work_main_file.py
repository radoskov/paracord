"""Add works.main_file_id for one-click "Read" of a paper's primary file (#16).

Nullable soft pointer (ON DELETE SET NULL) to a file; when NULL the UI uses the first attached file.

Revision ID: 0038_work_main_file
Revises: 0037_default_shelf
Create Date: 2026-07-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0038_work_main_file"
down_revision: str | None = "0037_default_shelf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("works", sa.Column("main_file_id", sa.Uuid(), nullable=True))
    # FK with ON DELETE SET NULL, Postgres-only (SQLite can't ALTER ADD CONSTRAINT); best-effort.
    if op.get_bind().dialect.name == "postgresql":
        op.create_foreign_key(
            "fk_works_main_file_id",
            "works",
            "files",
            ["main_file_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.drop_constraint("fk_works_main_file_id", "works", type_="foreignkey")
    op.drop_column("works", "main_file_id")
