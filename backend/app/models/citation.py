"""Reference and citation-context models."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Integer, String, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# A JSON column that becomes JSONB on Postgres (for @>/-> queries) and plain JSON on SQLite.
_JSONB = JSON().with_variant(JSONB(), "postgresql")


class Reference(Base):
    """Bibliographic reference extracted from a citing work."""

    __tablename__ = "references"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    citing_work_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    resolved_work_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), nullable=True, index=True
    )
    raw_citation: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    doi: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    arxiv_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    resolution_status: Mapped[str] = mapped_column(String(32), default="unresolved", index=True)
    source_tei_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class CitationMention(Base):
    """Specific in-text citation mention and its context."""

    __tablename__ = "citation_mentions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    citing_work_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    reference_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    resolved_cited_work_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), nullable=True, index=True
    )
    marker_text: Mapped[str | None] = mapped_column(String(128), nullable=True)
    section_label: Mapped[str | None] = mapped_column(String(255), nullable=True)
    context_before: Mapped[str | None] = mapped_column(Text, nullable=True)
    context_sentence: Mapped[str | None] = mapped_column(Text, nullable=True)
    context_after: Mapped[str | None] = mapped_column(Text, nullable=True)
    page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # PDF coordinate boxes from GROBID teiCoordinates, as a list of
    # {"page", "x", "y", "w", "h"} dicts (multi-box spans supported). Replaces the four
    # scalar pdf_* columns so a mention can anchor across line wraps (SPEC §9.3).
    pdf_coordinates: Mapped[list[dict[str, Any]] | None] = mapped_column(_JSONB, nullable=True)
    source_tei_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class RawTeiDocument(Base):
    """Raw TEI XML from an extraction run for future reprocessing."""

    __tablename__ = "raw_tei_documents"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    file_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    work_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    source: Mapped[str] = mapped_column(String(128), default="grobid", index=True)
    tei_xml: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True
    )
