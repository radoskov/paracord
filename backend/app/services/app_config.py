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
from app.models.app_config import (
    _DEFAULT_MAX_BATCH_ITEMS,
    _DEFAULT_RATE_LIMIT_GLOBAL_PER_MIN,
    _DEFAULT_RATE_LIMIT_PER_CLIENT_PER_MIN,
    APP_CONFIG_SINGLETON_ID,
    AppConfig,
)


class BatchTooLargeError(Exception):
    """A client import batch exceeded the configured ``max_batch_items`` cap (D1)."""

    def __init__(self, *, limit: int, count: int) -> None:
        self.limit = limit
        self.count = count
        super().__init__(
            f"Batch of {count} exceeds the {limit}-item limit; split it into smaller imports"
        )


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


def effective_rate_limit_per_client_per_min(
    db: Session, *, settings: Settings | None = None
) -> int:
    """Return the effective per-client request rate-limit ceiling (requests per rolling minute)."""
    if not _app_config_table_present(db):
        return _DEFAULT_RATE_LIMIT_PER_CLIENT_PER_MIN
    row = db.get(AppConfig, APP_CONFIG_SINGLETON_ID)
    if row is None or row.rate_limit_per_client_per_min is None:
        return _DEFAULT_RATE_LIMIT_PER_CLIENT_PER_MIN
    return row.rate_limit_per_client_per_min


def effective_rate_limit_global_per_min(db: Session, *, settings: Settings | None = None) -> int:
    """Return the effective global request rate-limit ceiling (requests per rolling minute)."""
    if not _app_config_table_present(db):
        return _DEFAULT_RATE_LIMIT_GLOBAL_PER_MIN
    row = db.get(AppConfig, APP_CONFIG_SINGLETON_ID)
    if row is None or row.rate_limit_global_per_min is None:
        return _DEFAULT_RATE_LIMIT_GLOBAL_PER_MIN
    return row.rate_limit_global_per_min


def effective_max_batch_items(db: Session, *, settings: Settings | None = None) -> int:
    """Return the effective cap on items in a single client import batch (server scans exempt)."""
    if not _app_config_table_present(db):
        return _DEFAULT_MAX_BATCH_ITEMS
    row = db.get(AppConfig, APP_CONFIG_SINGLETON_ID)
    if row is None or row.max_batch_items is None:
        return _DEFAULT_MAX_BATCH_ITEMS
    return row.max_batch_items


def enforce_batch_limit(db: Session, count: int) -> None:
    """Raise :class:`BatchTooLargeError` when ``count`` exceeds the configured batch-item cap."""
    limit = effective_max_batch_items(db)
    if count > limit:
        raise BatchTooLargeError(limit=limit, count=count)


def _ensure_row(db: Session) -> AppConfig:
    row = db.get(AppConfig, APP_CONFIG_SINGLETON_ID)
    if row is None:
        row = AppConfig(id=APP_CONFIG_SINGLETON_ID)
        db.add(row)
    return row


def update_max_papers_per_page(
    db: Session, *, value: int, actor_user_id: uuid.UUID | None = None
) -> int:
    """Persist a new global maximum Library page size. Returns the stored value."""
    if value < 1:
        raise ValueError("max_papers_per_page must be >= 1")
    row = _ensure_row(db)
    row.max_papers_per_page = value
    row.updated_by_user_id = actor_user_id
    db.flush()
    return row.max_papers_per_page


def update_rate_limits(
    db: Session,
    *,
    per_client_per_min: int | None = None,
    global_per_min: int | None = None,
    actor_user_id: uuid.UUID | None = None,
) -> None:
    """Persist new request rate-limit ceilings (only the provided fields are changed)."""
    if per_client_per_min is not None and per_client_per_min < 1:
        raise ValueError("rate_limit_per_client_per_min must be >= 1")
    if global_per_min is not None and global_per_min < 1:
        raise ValueError("rate_limit_global_per_min must be >= 1")
    row = _ensure_row(db)
    if per_client_per_min is not None:
        row.rate_limit_per_client_per_min = per_client_per_min
    if global_per_min is not None:
        row.rate_limit_global_per_min = global_per_min
    row.updated_by_user_id = actor_user_id
    db.flush()


def update_max_batch_items(
    db: Session, *, value: int, actor_user_id: uuid.UUID | None = None
) -> int:
    """Persist a new client import-batch item cap. Returns the stored value."""
    if value < 1:
        raise ValueError("max_batch_items must be >= 1")
    row = _ensure_row(db)
    row.max_batch_items = value
    row.updated_by_user_id = actor_user_id
    db.flush()
    return row.max_batch_items
