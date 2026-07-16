"""Metadata provenance models.

Records *candidate* metadata values (e.g. a title/year/DOI seen from Crossref vs. a PDF
extraction vs. a manual edit) so multiple disagreeing sources can coexist without clobbering one
another. Several assertions may exist for the same (entity_type, entity_id, field_name); at most
one is normally flagged ``selected_as_canonical`` to record which value is currently in effect.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Float, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class MetadataAssertion(Base):
    """Candidate metadata value with source and confidence."""

    __tablename__ = "metadata_assertions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Polymorphic reference: (entity_type, entity_id) identifies the owning row (e.g. "work"),
    # rather than a FK, since assertions can target several unrelated entity tables.
    entity_type: Mapped[str] = mapped_column(String(64), index=True)
    entity_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    field_name: Mapped[str] = mapped_column(String(128), index=True)
    value: Mapped[str] = mapped_column(Text)
    # Where this candidate value came from, e.g. "crossref", "grobid", "manual".
    source: Mapped[str] = mapped_column(String(128), index=True)
    # Source-reported confidence in [0, 1], if the source provides one; NULL otherwise.
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    selected_as_canonical: Mapped[bool] = mapped_column(Boolean, default=False)
    retrieved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
