"""Physical file, location, and file-to-work mapping models."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class File(Base):
    """Physical file identity, usually a PDF."""

    __tablename__ = "files"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    sha256: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    size_bytes: Mapped[int] = mapped_column(Integer)
    mime_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    original_filename: Mapped[str | None] = mapped_column(Text, nullable=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    text_layer_quality: Mapped[str] = mapped_column(String(32), default="unknown")
    status: Mapped[str] = mapped_column(String(32), default="available", index=True)
    preview_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    text_fingerprint: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Location(Base):
    """Where a file can be found."""

    __tablename__ = "locations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    file_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("files.id", ondelete="CASCADE"), index=True
    )
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("sources.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    location_type: Mapped[str] = mapped_column(String(64))
    display_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    internal_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    path_alias: Mapped[str | None] = mapped_column(Text, nullable=True)
    agent_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        nullable=True,
        index=True,
    )
    is_available: Mapped[bool] = mapped_column(Boolean, default=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    last_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class FileSegment(Base):
    """Page range or segment inside a file."""

    __tablename__ = "file_segments"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    file_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("files.id", ondelete="CASCADE"), index=True
    )
    page_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    segment_type: Mapped[str] = mapped_column(String(64), default="full_file")
    created_by: Mapped[str] = mapped_column(String(32), default="system")
    confidence: Mapped[int] = mapped_column(Integer, default=100)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class FileWorkLink(Base):
    """Many-to-many link between files and works."""

    __tablename__ = "file_work_links"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    file_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("files.id", ondelete="CASCADE"), index=True
    )
    work_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("works.id", ondelete="CASCADE"), index=True
    )
    version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("work_versions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    segment_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("file_segments.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    relationship_type: Mapped[str] = mapped_column(String(64), default="primary")
    confidence: Mapped[int] = mapped_column(Integer, default=100)
    warning_state: Mapped[str] = mapped_column(String(128), default="none")
    user_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
