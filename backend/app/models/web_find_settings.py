"""Owner-managed global find-on-web settings (find-on-web v2).

A single-row settings table (id == :data:`WEB_FIND_SETTINGS_SINGLETON_ID`) holding the global
download-policy mode for find-on-web. The owner toggles this from the Admin UI; it is never read
from a config file at runtime. An absent row reproduces the conservative default (``restricted``).
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# Single-row primary key so there is at most one settings row (a singleton).
WEB_FIND_SETTINGS_SINGLETON_ID = uuid.UUID(int=1)

# The conservative default: only the merged allow-list may be downloaded from.
DEFAULT_DOWNLOAD_POLICY = "restricted"


class WebFindSettings(Base):
    """Owner-managed global find-on-web settings singleton.

    ``download_policy`` is one of ``restricted`` / ``careful`` / ``unrestricted``; an absent row
    (or NULL column) means ``restricted``.
    """

    __tablename__ = "web_find_settings"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=WEB_FIND_SETTINGS_SINGLETON_ID
    )
    download_policy: Mapped[str | None] = mapped_column(String(32), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
