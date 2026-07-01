"""Owner/admin-managed global access-control settings (Phase H).

A single-row settings table (id == :data:`ACCESS_SETTINGS_SINGLETON_ID`) holding the global
**default access level** applied to newly created racks/shelves. Mirrors the web-find settings
singleton pattern: an absent row (or NULL column) reproduces the conservative default
(``open``). The default personal-group grant set lives in the ``default_grants`` table, not here.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# Single-row primary key so there is at most one settings row (a singleton).
ACCESS_SETTINGS_SINGLETON_ID = uuid.UUID(int=2)

# The allowed access levels, conservative default first.
ACCESS_LEVELS = ("open", "visible", "private")
DEFAULT_ACCESS_LEVEL = "open"


class AccessSettings(Base):
    """Owner/admin-managed global access-control settings singleton.

    ``default_access_level`` is one of ``open`` / ``visible`` / ``private``; an absent row (or NULL
    column) means ``open``.
    """

    __tablename__ = "access_settings"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=ACCESS_SETTINGS_SINGLETON_ID
    )
    default_access_level: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # The ephemeral "default shelf" newly-added papers land on so nothing is free-floating (#1).
    # Soft reference (no FK, mirrors updated_by_user_id) to the shelf; NULL until bootstrapped.
    default_shelf_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
