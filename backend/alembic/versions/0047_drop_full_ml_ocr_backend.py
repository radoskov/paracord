"""Rewrite the removed ai_config.ocr_backend "full_ml" value to NULL (D35)

The ML-extraction seam (Nougat/Marker + the ``full_ml`` OCR backend) was removed; OCR backends are
``ocrmypdf`` and ``pymupdf`` only. A legacy row may still carry ``full_ml`` — rewrite it to NULL so
it falls back to the ``Settings.ocr_backend`` default. The runtime getter also tolerates the value
defensively; this migration just cleans the stored data. No downgrade (the value is gone).

Revision ID: 0047_drop_full_ml_ocr_backend
Revises: 0046_max_queue_len
Create Date: 2026-07-02
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0047_drop_full_ml_ocr_backend"
down_revision: str | None = "0046_max_queue_len"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    op.execute("UPDATE ai_config SET ocr_backend = NULL WHERE ocr_backend = 'full_ml'")


def downgrade() -> None:
    """Revert the migration (no-op: the ``full_ml`` value no longer exists)."""
