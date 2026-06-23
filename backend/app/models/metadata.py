"""Metadata provenance models."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class MetadataAssertion(Base):
    """Candidate metadata value with source and confidence."""

    __tablename__ = "metadata_assertions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entity_type: Mapped[str] = mapped_column(String(64), index=True)
    entity_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    field_name: Mapped[str] = mapped_column(String(128), index=True)
    value: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(128), index=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    selected_as_canonical: Mapped[bool] = mapped_column(Boolean, default=False)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
