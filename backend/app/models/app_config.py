"""Owner-managed runtime application configuration (D18).

A DB-backed settings singleton (mirrors :mod:`app.models.ai`'s ``AIConfig``) holding app-wide knobs
an owner/admin edits at runtime rather than via a config file. Currently just the global maximum
Library page size. A single row (id == :data:`APP_CONFIG_SINGLETON_ID`); an absent row means the
static ``Settings`` defaults apply.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, Integer, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# Single-row primary key so there is at most one app-config row (a settings singleton).
APP_CONFIG_SINGLETON_ID = uuid.UUID(int=1)

# Out-of-the-box global ceiling on the Library page size; mirrors ``Settings.max_papers_per_page``.
_DEFAULT_MAX_PAPERS_PER_PAGE = 500

# Out-of-the-box overload-protection defaults (D1). Rate limits are per rolling minute; a request
# exceeding either the per-client or the global ceiling is rejected with 429.
_DEFAULT_RATE_LIMIT_PER_CLIENT_PER_MIN = 60
_DEFAULT_RATE_LIMIT_GLOBAL_PER_MIN = 300

# Out-of-the-box ceiling on how many items a single client import batch may carry (D1). Server-folder
# scans (a local scan, not a client batch) are exempt from this cap.
_DEFAULT_MAX_BATCH_ITEMS = 100

# Out-of-the-box number of RQ extraction worker processes the supervisor launches (D1). Read once at
# worker-container start; changing it requires a worker restart to apply.
_DEFAULT_RQ_WORKER_COUNT = 2


class AppConfig(Base):
    """Owner-managed runtime application configuration (overlays the static ``Settings`` defaults).

    A single row (id == :data:`APP_CONFIG_SINGLETON_ID`). Edited from the Admin panel, never from a
    config file at runtime. An absent row reproduces the out-of-the-box ``Settings`` behaviour.
    """

    __tablename__ = "app_config"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=APP_CONFIG_SINGLETON_ID
    )
    # Global clamp on the Library page size (D18). Server default mirrors
    # ``Settings.max_papers_per_page`` so a freshly-inserted row keeps the built-in ceiling.
    max_papers_per_page: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=str(_DEFAULT_MAX_PAPERS_PER_PAGE)
    )
    # Overload protection (D1): shared Redis rate-limit ceilings per rolling minute.
    rate_limit_per_client_per_min: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=str(_DEFAULT_RATE_LIMIT_PER_CLIENT_PER_MIN)
    )
    rate_limit_global_per_min: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=str(_DEFAULT_RATE_LIMIT_GLOBAL_PER_MIN)
    )
    # Overload protection (D1): max items in a single client import batch (server scans are exempt).
    max_batch_items: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=str(_DEFAULT_MAX_BATCH_ITEMS)
    )
    # Overload protection (D1): RQ worker processes the supervisor launches (apply-on-restart).
    rq_worker_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=str(_DEFAULT_RQ_WORKER_COUNT)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
