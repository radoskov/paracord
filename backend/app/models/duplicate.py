"""Duplicate and version review queue models."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Float, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DuplicateCandidate(Base):
    """A potential duplicate/version relationship awaiting user review."""

    __tablename__ = "duplicate_candidates"
    __table_args__ = (
        UniqueConstraint(
            "candidate_type",
            "entity_a_type",
            "entity_a_id",
            "entity_b_type",
            "entity_b_id",
            name="uq_duplicate_candidate_pair",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    candidate_type: Mapped[str] = mapped_column(String(64), index=True)
    entity_a_type: Mapped[str] = mapped_column(String(64), index=True)
    entity_a_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    entity_b_type: Mapped[str] = mapped_column(String(64), index=True)
    entity_b_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    score: Mapped[float] = mapped_column(Float)
    signals: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="open", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    resolved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        nullable=True,
        index=True,
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
