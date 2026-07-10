"""Staging tables for multi-PDF import (batch 10, issue 1).

A multi-PDF upload extracts each PDF **before** any ``Work``/``FileWorkLink`` is created, so the
user can preview the extracted metadata and detected collisions and choose which papers to create.
The staged PDF bytes are stored content-addressed immediately (dedup-safe, reused on commit); the
extracted TEI + parsed metadata live on the staging item until commit mints the real records.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# A JSON column that becomes JSONB on Postgres and plain JSON on SQLite.
_JSONB = JSON().with_variant(JSONB(), "postgresql")


class ImportStagingBatch(Base):
    """One multi-PDF import session awaiting (or having completed) a commit."""

    __tablename__ = "import_staging_batches"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), nullable=True, index=True
    )
    # "preview" = user commits explicitly; "direct" = auto-commit non-blocked items on extraction.
    mode: Mapped[str] = mapped_column(String(16), default="preview")
    # "extracting" → "ready" → "committed" / "cancelled".
    status: Mapped[str] = mapped_column(String(32), default="extracting", index=True)
    target_shelf_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class ImportStagingItem(Base):
    """One staged PDF: its content-addressed file, extraction outcome, and detected collisions."""

    __tablename__ = "import_staging_items"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    batch_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("import_staging_batches.id", ondelete="CASCADE"),
        index=True,
    )
    # The content-addressed File already stored for this PDF (SET NULL if the file is later removed).
    file_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("files.id", ondelete="SET NULL"), nullable=True, index=True
    )
    filename: Mapped[str] = mapped_column(Text, default="upload.pdf")
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # "pending" → "extracting" → "extracted" | "extract_failed"; then "committed" | "skipped".
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Preview metadata parsed from the TEI: {title, authors, year, doi, venue, abstract}.
    parsed: Mapped[dict | None] = mapped_column(_JSONB, nullable=True)
    # Raw GROBID TEI, kept so commit can apply the full extraction without re-running GROBID.
    tei_xml: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Detected collisions against existing papers: {same_pdf: [...], same_doi: [...], same_title: [...]}
    # where each entry is {work_id, title}.
    duplicates: Mapped[dict | None] = mapped_column(_JSONB, nullable=True)
    # The Work created on commit (NULL until committed / if skipped).
    created_work_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
