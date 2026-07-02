"""Effective application configuration (D18).

Resolves runtime app-wide knobs by overlaying the owner-editable ``app_config`` DB row on the static
``Settings`` defaults (DB wins; an absent row reproduces the out-of-the-box behaviour). Currently
just the global maximum Library page size. Uses the same table-presence guard as
:mod:`app.services.ai_config` so narrow unit-test schemas that omit the table don't break, and a read
never rolls back the caller's transaction.
"""

from __future__ import annotations

import uuid

from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models.app_config import APP_CONFIG_SINGLETON_ID, AppConfig

# Per-engine memo of whether the ``app_config`` table exists (narrow unit-test schemas omit it).
_TABLE_PRESENT: dict[int, bool] = {}


def _app_config_table_present(db: Session) -> bool:
    bind = db.get_bind()
    key = id(bind)
    if key not in _TABLE_PRESENT:
        # Inspect the session's own connection rather than the engine (see ai_config for why): this
        # keeps the caller's uncommitted rows and pending flush intact.
        _TABLE_PRESENT[key] = inspect(db.connection()).has_table(AppConfig.__tablename__)
    return _TABLE_PRESENT[key]


def effective_max_papers_per_page(db: Session, *, settings: Settings | None = None) -> int:
    """Return the effective global maximum Library page size (DB row value, else Settings default)."""
    settings = settings or get_settings()
    if not _app_config_table_present(db):
        return settings.max_papers_per_page
    row = db.get(AppConfig, APP_CONFIG_SINGLETON_ID)
    if row is None or row.max_papers_per_page is None:
        return settings.max_papers_per_page
    return row.max_papers_per_page


def update_max_papers_per_page(
    db: Session, *, value: int, actor_user_id: uuid.UUID | None = None
) -> int:
    """Persist a new global maximum Library page size. Returns the stored value."""
    if value < 1:
        raise ValueError("max_papers_per_page must be >= 1")
    row = db.get(AppConfig, APP_CONFIG_SINGLETON_ID)
    if row is None:
        row = AppConfig(id=APP_CONFIG_SINGLETON_ID)
        db.add(row)
    row.max_papers_per_page = value
    row.updated_by_user_id = actor_user_id
    db.flush()
    return row.max_papers_per_page
