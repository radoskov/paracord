"""Per-model constrained pgvector columns + HNSW indexes on work_chunks (HS2)

Postgres-only and best-effort (mirrors 0019_pgvector): if the ``vector`` extension is unavailable
the migration logs and returns, leaving ``work_chunks`` as the dialect-agnostic table from 0034.
Each supported embedding model gets its own **dimension-constrained** column so a real ANN index
(HNSW, cosine) can be built — an unconstrained column cannot be ANN-indexed. A column is bound to
exactly one model (vectors from different models are never comparable); adding a model later is a
new column here plus a registry entry in ``services/chunk_embeddings.py``.

Revision ID: 0035_work_chunk_vectors
Revises: 0034_work_chunks
Create Date: 2026-07-01
"""

import logging
from collections.abc import Sequence

from alembic import op

revision: str = "0035_work_chunk_vectors"
down_revision: str | None = "0034_work_chunks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")

# (column, dimension) per supported model. Keep in sync with CHUNK_MODEL_COLUMNS.
_COLUMNS = (("vec_minilm", 384), ("vec_nomic", 768))


def upgrade() -> None:
    """Apply the migration (Postgres + pgvector only; otherwise a no-op)."""
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    try:
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    except Exception as exc:  # noqa: BLE001 - image may lack pgvector; degrade to no chunk ANN
        logger.warning("pgvector unavailable (%s); skipping work_chunks vector columns.", exc)
        return
    for column, dim in _COLUMNS:
        op.execute(f"ALTER TABLE work_chunks ADD COLUMN IF NOT EXISTS {column} vector({dim})")
        # HNSW cosine index (pgvector >= 0.5). Best-effort: an older pgvector without HNSW leaves
        # the column usable via exact scan rather than failing the upgrade.
        try:
            with op.get_context().autocommit_block():
                op.execute(
                    f"CREATE INDEX IF NOT EXISTS ix_work_chunks_{column} "
                    f"ON work_chunks USING hnsw ({column} vector_cosine_ops)"
                )
        except Exception as exc:  # noqa: BLE001 - no HNSW support; exact scan still works
            logger.warning("Could not build HNSW index on work_chunks.%s (%s).", column, exc)


def downgrade() -> None:
    """Revert the migration (Postgres only)."""
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    for column, _dim in _COLUMNS:
        op.execute(f"DROP INDEX IF EXISTS ix_work_chunks_{column}")
        op.execute(f"ALTER TABLE work_chunks DROP COLUMN IF EXISTS {column}")
