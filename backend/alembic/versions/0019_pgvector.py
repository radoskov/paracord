"""Optional pgvector acceleration for embeddings (H7, WORKPLAN_NEXT Stage 9)

Creates the ``vector`` extension and adds a Postgres-only ``embeddings.vector_pg`` column (an
**unconstrained** pgvector column, so it stores any provider's dimension; queries always filter by
``model_name`` first, so only same-dimension vectors are ever compared). The JSON ``vector`` column
remains the source of truth and the SQLite/default path; ``vector_pg`` is an additive query
accelerator used only when ``pgvector_enabled`` is set.

Postgres-only and best-effort: if the ``vector`` extension cannot be created (image without
pgvector), the migration logs and skips the column rather than failing the upgrade.

Revision ID: 0019_pgvector
Revises: 0018_ai_config
Create Date: 2026-06-30
"""

import logging
from collections.abc import Sequence

from alembic import op

revision: str = "0019_pgvector"
down_revision: str | None = "0018_ai_config"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")


def upgrade() -> None:
    """Apply the migration (Postgres + pgvector only; otherwise a no-op)."""
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    try:
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    except Exception as exc:  # noqa: BLE001 - image may lack pgvector; degrade to JSON-only path
        logger.warning("pgvector extension unavailable (%s); skipping vector_pg column.", exc)
        return
    # Unconstrained vector column (no fixed dim) — searches filter by model_name first, so only
    # same-dimension vectors are ever compared by the `<=>` operator.
    op.execute("ALTER TABLE embeddings ADD COLUMN IF NOT EXISTS vector_pg vector")


def downgrade() -> None:
    """Revert the migration (Postgres only)."""
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    with op.get_context().autocommit_block():
        op.execute("ALTER TABLE embeddings DROP COLUMN IF EXISTS vector_pg")
