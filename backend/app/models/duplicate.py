"""Duplicate and version review queue models."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Float, String, UniqueConstraint, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

_JSONB = JSON().with_variant(JSONB(), "postgresql")


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
    # Kind of relationship being flagged (e.g. "duplicate_work" / "version"), not a table name.
    candidate_type: Mapped[str] = mapped_column(String(64), index=True)
    # Generic polymorphic reference (entity_type + entity_id) rather than a typed FK, since a
    # candidate pair can span different entity kinds (e.g. work vs work_version); the app layer
    # resolves entity_*_type/id to the right table.
    entity_a_type: Mapped[str] = mapped_column(String(64), index=True)
    entity_a_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    entity_b_type: Mapped[str] = mapped_column(String(64), index=True)
    entity_b_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    score: Mapped[float] = mapped_column(Float)
    # Per-signal breakdown backing the score (e.g. title/author overlap) for display + tuning.
    signals: Mapped[dict[str, Any]] = mapped_column(_JSONB, default=dict)
    # 'open' (awaiting review) | resolved outcome set by the review action (e.g. merged/dismissed).
    status: Mapped[str] = mapped_column(String(32), default="open", index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    # No FK: an audit-only reference to the reviewing user, kept even if the user is later deleted.
    resolved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        nullable=True,
        index=True,
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
