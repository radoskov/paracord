"""Incoming external citations — papers (from an open database) that cite a work (batch 10, #8).

Distinct from ``Reference`` (which models the *outgoing* direction: this work cites X). An
``ExternalCitation`` row is a paper *out there* that cites one of our works, fetched on demand from
OpenAlex (falling back to Semantic Scholar) and cached so it can drive the paper-view "Citing papers"
panel and the incoming side of the reference graph. Refetching replaces the work's rows.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ExternalCitation(Base):
    """One external paper that cites a given work (incoming citation)."""

    __tablename__ = "external_citations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    work_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("works.id", ondelete="CASCADE"), index=True
    )
    # Which open database supplied this citing paper ("openalex" / "semanticscholar").
    source: Mapped[str] = mapped_column(String(32), index=True)
    # The citing paper's id at that source (OpenAlex work id / S2 paper id); may be null.
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Authors as a "; "-joined display string (matches the authors metadata convention).
    authors: Mapped[str | None] = mapped_column(Text, nullable=True)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    doi: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    venue: Mapped[str | None] = mapped_column(Text, nullable=True)
    # When this row was fetched (all of a work's rows share a fetch timestamp → "as of" display).
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
