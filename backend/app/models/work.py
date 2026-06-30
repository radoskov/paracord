"""Work and version models."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

_JSONB = JSON().with_variant(JSONB(), "postgresql")


class Work(Base):
    """Conceptual scholarly work."""

    __tablename__ = "works"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    canonical_title: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    normalized_title: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    abstract: Mapped[str | None] = mapped_column(Text, nullable=True)
    doi: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    arxiv_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    arxiv_base_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    venue: Mapped[str | None] = mapped_column(Text, nullable=True)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    work_type: Mapped[str] = mapped_column(String(64), default="unknown", index=True)
    canonical_metadata_source: Mapped[str | None] = mapped_column(String(128), nullable=True)
    reading_status: Mapped[str] = mapped_column(String(64), default="unread", index=True)
    # Manual ordering within the reading queue (SPEC §8.17.1); NULL sorts last.
    queue_position: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    user_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    # Per-field user confirmation (SPEC §8.12): names of fields the user has locked so enrichment
    # never overwrites them (e.g. ["title", "year"]). Supersedes the all-or-nothing user_confirmed.
    confirmed_fields: Mapped[list | None] = mapped_column(_JSONB, default=list)
    # Deterministic keyphrases from extraction (SPEC §8.15.1), most salient first.
    keywords: Mapped[list | None] = mapped_column(_JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class WorkVersion(Base):
    """Specific version of a work."""

    __tablename__ = "work_versions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    work_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("works.id", ondelete="CASCADE"), index=True
    )
    version_label: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source: Mapped[str | None] = mapped_column(String(128), nullable=True)
    publication_state: Mapped[str | None] = mapped_column(String(64), nullable=True)
    version_type: Mapped[str] = mapped_column(String(64), default="unknown")
    arxiv_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    doi: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
