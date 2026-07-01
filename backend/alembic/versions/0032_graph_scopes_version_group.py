"""Add works.import_batch_id + works.version_group_id (Phase B6, graph scopes + version collapse)

Two additive, nullable, indexed columns on ``works``:

* ``import_batch_id`` — FK ``import_batches.id`` ``ondelete=SET NULL`` (deleting a batch never
  cascades to the paper). Populated at batch work-creation sites; backs the ``import_batch``
  citation-graph scope. Not backfilled: pre-existing works stay NULL and simply don't appear in
  any batch-scoped graph.
* ``version_group_id`` — plain grouping key, no FK (a work may point at its own id). Works linked
  as versions share the representative work's id; backs graph version-collapse.

``version_group_id`` gets a best-effort backfill: every work that is the *target* of a
``work_versions`` row is its own group representative, so ``version_group_id = works.id`` there.
Pre-existing *source* works (the linked-as-version originals) carry no back-link in
``work_versions`` and therefore cannot be retro-grouped — they stay NULL. Grouping is correct going
forward (both target and source are set at link time by ``_link_work_candidate_as_version``).

Revision ID: 0032_graph_scopes_version_group
Revises: 0031_ai_config_ocr_backend
Create Date: 2026-07-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0032_graph_scopes_version_group"
down_revision: str | None = "0031_ai_config_ocr_backend"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    op.add_column(
        "works",
        sa.Column(
            "import_batch_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("import_batches.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column(
        "works",
        sa.Column("version_group_id", sa.Uuid(as_uuid=True), nullable=True),
    )
    op.create_index(op.f("ix_works_import_batch_id"), "works", ["import_batch_id"], unique=False)
    op.create_index(op.f("ix_works_version_group_id"), "works", ["version_group_id"], unique=False)
    # Best-effort version-group backfill: every work that is a version target is its own
    # representative. Source works have no back-link and stay NULL (see module docstring).
    op.execute(
        """
        UPDATE works
        SET version_group_id = works.id
        WHERE works.id IN (SELECT DISTINCT work_id FROM work_versions)
        """
    )


def downgrade() -> None:
    """Revert the migration."""
    op.drop_index(op.f("ix_works_version_group_id"), table_name="works")
    op.drop_index(op.f("ix_works_import_batch_id"), table_name="works")
    op.drop_column("works", "version_group_id")
    op.drop_column("works", "import_batch_id")
