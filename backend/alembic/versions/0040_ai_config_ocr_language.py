"""Add ai_config.ocr_language (configurable multi-language OCR)

Additive, nullable column: the owner-selectable OCR languages in tesseract syntax (e.g. "eng" or
"eng+spa"). NULL falls back to the ``Settings.ocr_language`` default, so an empty/absent row keeps
the out-of-the-box single-language behaviour.

Revision ID: 0040_ai_config_ocr_language
Revises: 0039_access_indexes
Create Date: 2026-07-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0040_ai_config_ocr_language"
down_revision: str | None = "0039_access_indexes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    op.add_column("ai_config", sa.Column("ocr_language", sa.String(length=128), nullable=True))


def downgrade() -> None:
    """Revert the migration."""
    op.drop_column("ai_config", "ocr_language")
