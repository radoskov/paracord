"""Configured import sources and import batch tracking."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, String, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# A JSON column that becomes JSONB on Postgres (for @>/-> queries) and plain JSON on SQLite.
_JSONB = JSON().with_variant(JSONB(), "postgresql")


class Source(Base):
    """A configured place or identifier source that can produce files or works."""

    __tablename__ = "sources"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    type: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        nullable=True,
        index=True,
    )
    # Set when this source is backed by a paired Teleport Agent rather than a server-local path
    # or a plain URL.
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        nullable=True,
        index=True,
    )
    # For type="server_folder": the alias into the merged server-roots map (yaml + ImportRoot)
    # this source was created from. See app.services.storage.merged_server_roots.
    path_alias: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    # sha256 of the resolved absolute root path at creation time, so a later alias-to-path remap
    # (yaml edit or ImportRoot change) can be detected instead of silently importing from a
    # different directory under the same alias.
    canonical_root_hash: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    config: Mapped[dict | None] = mapped_column(_JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)


class ImportBatch(Base):
    """One import activity and its outcome statistics."""

    __tablename__ = "import_batches"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        nullable=True,
        index=True,
    )
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("sources.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        nullable=True,
        index=True,
    )
    input_type: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(64), default="queued", index=True)
    settings: Mapped[dict | None] = mapped_column(_JSONB, nullable=True)
    stats: Mapped[dict | None] = mapped_column(_JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
