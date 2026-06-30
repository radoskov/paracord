"""Security role redesign: single immutable owner + new admin role (batch 2 #20)

Roles become ``owner`` > ``admin`` > ``editor`` > ``reader``.

- ``role`` is stored as a plain ``VARCHAR(32)`` (no native Postgres enum), so adding the ``admin``
  value needs no ``ALTER TYPE``; the application layer constrains the allowed values.
- Adds ``users.is_bootstrap`` — the marker for the single, immutable owner account.
- Data migration: pick exactly one surviving owner (the existing bootstrap account if one is already
  flagged, otherwise the earliest-created owner), set its ``is_bootstrap`` flag, and downgrade ALL
  other current ``owner`` accounts to ``admin``. This removes the multi-owner self-lockout hazard.

Revision ID: 0024_role_redesign
Revises: 0023_work_keywords
Create Date: 2026-06-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024_role_redesign"
down_revision: str | None = "0023_work_keywords"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the migration."""
    # 1. Add the immutability marker (NOT NULL, default False) for the single owner.
    op.add_column(
        "users",
        sa.Column(
            "is_bootstrap",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )

    conn = op.get_bind()

    # 2. Choose the surviving owner deterministically: an already-flagged bootstrap account wins;
    #    otherwise the earliest-created owner. Tie-break on id for stability.
    surviving = conn.execute(
        sa.text(
            "SELECT id FROM users WHERE role = 'owner' "
            "ORDER BY is_bootstrap DESC, created_at ASC, id ASC LIMIT 1"
        )
    ).scalar()

    if surviving is not None:
        # 3. Flag the surviving owner as the bootstrap account.
        conn.execute(
            sa.text("UPDATE users SET is_bootstrap = true WHERE id = :id"),
            {"id": surviving},
        )
        # 4. Downgrade every OTHER current owner to the new admin role.
        conn.execute(
            sa.text("UPDATE users SET role = 'admin' WHERE role = 'owner' AND id <> :id"),
            {"id": surviving},
        )

    # Drop the server-side default now that the column is populated; the ORM supplies it.
    op.alter_column("users", "is_bootstrap", server_default=None)


def downgrade() -> None:
    """Revert the migration.

    Restores the legacy model where any ``admin`` is an ``owner`` again (the original flaw). The
    bootstrap flag is dropped. This is intentionally lossy: the pre-migration owner set cannot be
    reconstructed, but every former admin becomes an owner, which is the prior behaviour.
    """
    conn = op.get_bind()
    conn.execute(sa.text("UPDATE users SET role = 'owner' WHERE role = 'admin'"))
    op.drop_column("users", "is_bootstrap")
