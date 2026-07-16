"""Reader annotation models."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Integer, String, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

_JSONB = JSON().with_variant(JSONB(), "postgresql")


class Annotation(Base):
    """Annotation stored separately from the source PDF."""

    __tablename__ = "annotations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    work_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    file_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True, index=True)
    # Which specific file version (if the work has multiple) the annotation's page/coordinates were
    # captured against; soft reference, no FK (mirrors other *_id columns in this model).
    version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        nullable=True,
        index=True,
    )
    page: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    # Reader-defined shape (e.g. highlight rectangle(s)) in PDF viewer coordinate space; opaque to
    # the backend, interpreted only by the frontend reader component.
    coordinates: Mapped[dict[str, Any] | None] = mapped_column(_JSONB, nullable=True)
    selected_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # e.g. "highlight" | "note" | "comment" — drives how the frontend renders/edits the annotation.
    annotation_type: Mapped[str] = mapped_column(String(64), index=True)
    content_markdown: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
