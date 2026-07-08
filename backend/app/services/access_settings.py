"""Owner/admin-managed global access-control settings (Phase H default access level).

Stores the global default access level (applied to newly created racks/shelves) in the single-row
``access_settings`` table. An absent row (or NULL column) reproduces the conservative ``open``
default. Mirrors ``app.services.web_find_settings``: a read helper must never provoke + roll back an
error inside the caller's transaction, so the table presence is probed and memoized per engine.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.models.access_settings import (
    ACCESS_LEVELS,
    ACCESS_SETTINGS_SINGLETON_ID,
    DEFAULT_ACCESS_LEVEL,
    AccessSettings,
)
from app.utils.table_presence import table_present


def _table_present(db: Session) -> bool:
    """Whether the ``access_settings`` table exists (narrow unit-test schemas omit it)."""
    return table_present(db, AccessSettings.__tablename__)


def get_default_access_level(db: Session) -> str:
    """Return the effective global default access level (DB row, else the default)."""
    if not _table_present(db):
        return DEFAULT_ACCESS_LEVEL
    row = db.get(AccessSettings, ACCESS_SETTINGS_SINGLETON_ID)
    if row is None or not row.default_access_level:
        return DEFAULT_ACCESS_LEVEL
    return row.default_access_level


def set_default_access_level(
    db: Session, *, access_level: str, actor_user_id: uuid.UUID | None = None
) -> str:
    """Validate + persist the global default access level. Returns the stored value.

    Raises ``ValueError`` for an unknown level. The caller commits.
    """
    normalized = (access_level or "").strip().lower()
    if normalized not in ACCESS_LEVELS:
        raise ValueError(f"Unknown access level (allowed: {ACCESS_LEVELS})")
    row = db.get(AccessSettings, ACCESS_SETTINGS_SINGLETON_ID)
    if row is None:
        row = AccessSettings(id=ACCESS_SETTINGS_SINGLETON_ID)
        db.add(row)
    row.default_access_level = normalized
    row.updated_by_user_id = actor_user_id
    db.flush()
    return normalized
