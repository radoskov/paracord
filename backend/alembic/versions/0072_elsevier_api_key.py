"""Elsevier Article Retrieval API key on the app-config singleton (UX batch 3).

Nullable, no server default: NULL falls back to the yaml/env value (PARACORD_ELSEVIER_API_KEY).

Revision ID: 0072_elsevier_api_key
Revises: 0071_citation_summary_cap
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0072_elsevier_api_key"
down_revision: str | None = "0071_citation_summary_cap"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("app_config", sa.Column("elsevier_api_key", sa.String(128), nullable=True))


def downgrade() -> None:
    op.drop_column("app_config", "elsevier_api_key")
