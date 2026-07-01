"""Default shelf: no free-floating papers (#1).

Adds ``access_settings.default_shelf_id``, creates the ephemeral default shelf ("Inbox") at the
global default access level, points the settings singleton at it, and retroactively places every
currently loose paper (on no shelf) onto it — so the "loose = open" special case no longer occurs
in normal operation. Dialect-agnostic (runs on SQLite tests too).

Revision ID: 0037_default_shelf
Revises: 0036_embedding_model_registry
Create Date: 2026-07-02
"""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "0037_default_shelf"
down_revision: str | None = "0036_embedding_model_registry"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ACCESS_SETTINGS_SINGLETON_ID = uuid.UUID(int=2)
DEFAULT_SHELF_NAME = "Inbox"


def upgrade() -> None:
    op.add_column("access_settings", sa.Column("default_shelf_id", sa.Uuid(), nullable=True))

    bind = op.get_bind()
    now = datetime.now(UTC)
    # Bind native UUID objects (Postgres uuid columns reject string literals without a cast).
    settings_id = ACCESS_SETTINGS_SINGLETON_ID
    # Resolve the configured default access level (open if unset), so the default shelf inherits it.
    row = bind.execute(
        sa.text("SELECT default_access_level FROM access_settings WHERE id = :id"),
        {"id": settings_id},
    ).first()
    access_level = (row[0] if row and row[0] else "open") if row is not None else "open"

    shelf_id = uuid.uuid4()
    op.execute(
        sa.text(
            "INSERT INTO shelves (id, name, access_level, created_at, updated_at) "
            "VALUES (:id, :name, :access_level, :now, :now)"
        ).bindparams(id=shelf_id, name=DEFAULT_SHELF_NAME, access_level=access_level, now=now)
    )

    # Point the settings singleton at the default shelf (create the row if absent).
    updated = bind.execute(
        sa.text("UPDATE access_settings SET default_shelf_id = :shelf WHERE id = :id"),
        {"shelf": shelf_id, "id": settings_id},
    )
    if updated.rowcount == 0:
        op.execute(
            sa.text(
                "INSERT INTO access_settings (id, default_shelf_id, updated_at) "
                "VALUES (:id, :shelf, :now)"
            ).bindparams(id=settings_id, shelf=shelf_id, now=now)
        )

    # Retroactively place every loose paper (on no shelf) onto the default shelf.
    op.execute(
        sa.text(
            "INSERT INTO shelf_works (shelf_id, work_id, added_at) "
            "SELECT :shelf, w.id, :now FROM works w "
            "WHERE NOT EXISTS (SELECT 1 FROM shelf_works sw WHERE sw.work_id = w.id)"
        ).bindparams(shelf=shelf_id, now=now)
    )


def downgrade() -> None:
    bind = op.get_bind()
    row = bind.execute(
        sa.text("SELECT default_shelf_id FROM access_settings WHERE id = :id"),
        {"id": ACCESS_SETTINGS_SINGLETON_ID},
    ).first()
    shelf_id = row[0] if row else None
    if shelf_id is not None:
        op.execute(sa.text("DELETE FROM shelf_works WHERE shelf_id = :id").bindparams(id=shelf_id))
        op.execute(sa.text("DELETE FROM shelves WHERE id = :id").bindparams(id=shelf_id))
    op.drop_column("access_settings", "default_shelf_id")
