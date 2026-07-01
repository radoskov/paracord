"""Add ai_config.ocr_backend (Phase B5, OCR / advanced-extraction backend)

Additive, nullable column: the owner-selectable OCR / advanced-extraction backend
(none | ocrmypdf | full_ml). NULL falls back to the ``Settings.ocr_backend`` default, so an
empty/absent row keeps the out-of-the-box OCRmyPDF pre-step behaviour.

Revision ID: 0031_ai_config_ocr_backend
Revises: 0030_work_topics
Create Date: 2026-07-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0031_ai_config_ocr_backend"
down_revision: str | None = "0030_work_topics"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    op.add_column("ai_config", sa.Column("ocr_backend", sa.String(length=64), nullable=True))


def downgrade() -> None:
    """Revert the migration."""
    op.drop_column("ai_config", "ocr_backend")
