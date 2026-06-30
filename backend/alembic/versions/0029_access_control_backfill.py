"""Access-control foundation backfill (Phase H)

Data migration paired with ``0028_access_control_schema``: create a **personal group** for every
existing user (named == their username) plus the matching membership row. Usernames are unique, so
the personal-group name normally collides only with a shared group an admin might already have made
in a partially-migrated DB; we resolve such a collision deterministically by appending
``-{user_id_prefix}`` to the personal group's name (and fail loud only if that, too, somehow
collides, which is effectively impossible).

Existing works keep ``created_by_user_id = NULL`` (treated as loose/open by the access layer). No
``access_settings`` row is created (absent row == the ``open`` default).

Revision ID: 0029_access_control_backfill
Revises: 0028_access_control_schema
Create Date: 2026-07-01
"""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "0029_access_control_backfill"
down_revision: str | None = "0028_access_control_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create a personal group + membership for every existing user."""
    conn = op.get_bind()
    now = datetime.now(UTC)

    users = conn.execute(
        sa.text("SELECT id, username FROM users ORDER BY created_at ASC, id ASC")
    ).all()

    # Names already taken (pre-existing groups, plus the ones we create in this loop).
    taken = {row[0] for row in conn.execute(sa.text("SELECT name FROM groups")).all()}

    for user_id, username in users:
        # Skip if this user somehow already has a personal group (idempotent-ish re-runs).
        existing = conn.execute(
            sa.text("SELECT id FROM groups WHERE personal_user_id = :uid"),
            {"uid": user_id},
        ).scalar()
        if existing is not None:
            continue

        name = username
        if name in taken:
            # Deterministic collision suffix from the user id; fail loud if even that collides.
            suffix = str(user_id).replace("-", "")[:8]
            name = f"{username}-{suffix}"
            if name in taken:
                raise RuntimeError(
                    f"Cannot create a personal group for user {username!r}: "
                    f"both {username!r} and {name!r} are already taken"
                )
        taken.add(name)

        group_id = uuid.uuid4()
        conn.execute(
            sa.text(
                "INSERT INTO groups (id, name, is_personal, personal_user_id, created_at, "
                "updated_at) VALUES (:id, :name, true, :uid, :now, :now)"
            ),
            {"id": group_id, "name": name, "uid": user_id, "now": now},
        )
        conn.execute(
            sa.text(
                "INSERT INTO group_memberships (group_id, user_id, added_by_user_id, added_at) "
                "VALUES (:gid, :uid, NULL, :now)"
            ),
            {"gid": group_id, "uid": user_id, "now": now},
        )


def downgrade() -> None:
    """Remove every personal group (memberships cascade via FK)."""
    conn = op.get_bind()
    conn.execute(sa.text("DELETE FROM groups WHERE is_personal = true"))
