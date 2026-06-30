"""Per-field user-confirmation locking on works (SPEC §8.12; AUDIT P2/item8)

Adds ``works.confirmed_fields`` (JSONB list of field names the user has locked) so external
enrichment never overwrites a field the user confirmed — replacing the all-or-nothing
``user_confirmed`` boolean for overwrite decisions (the boolean is retained).

Revision ID: 0021_work_confirmed_fields
Revises: 0020_user_agent_profile_fields
Create Date: 2026-06-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0021_work_confirmed_fields"
down_revision: str | None = "0020_user_agent_profile_fields"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSONB = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    """Apply the migration."""
    op.add_column("works", sa.Column("confirmed_fields", _JSONB, nullable=True))


def downgrade() -> None:
    """Revert the migration."""
    op.drop_column("works", "confirmed_fields")
