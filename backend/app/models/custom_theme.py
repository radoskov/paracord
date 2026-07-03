"""Admin-managed custom GUI theme (Theming P4).

A :class:`CustomTheme` is a hand-authored theme uploaded as YAML text by an owner/admin at runtime
— no rebuild required (unlike the four bundled themes, which are compiled from
``frontend/themes/*.yaml`` into the frontend at build time). The canonical source is ``yaml_source``;
``slug``/``name``/``mode``/``temperature`` are denormalised out of it at upload time so the picker
list can be served without re-parsing. The resolved token/graph object is re-derived from the YAML
on read (see ``app.core.theme_schema``). Storing the YAML in the DB keeps custom themes inside the
normal database backup, unlike a directory on the storage volume.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CustomTheme(Base):
    """A runtime-managed, hand-edited theme (YAML source + denormalised picker fields)."""

    __tablename__ = "custom_themes"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # The theme id used as ``data-theme`` on <html> and as the picker option id. Unique across
    # custom themes and (enforced in the service) never equal to a bundled theme id.
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)  # light | dark
    temperature: Mapped[str] = mapped_column(String(16), nullable=False, default="custom")
    yaml_source: Mapped[str] = mapped_column(Text, nullable=False)
    # The uploading admin; SET NULL on user delete so the theme survives its author being removed.
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
