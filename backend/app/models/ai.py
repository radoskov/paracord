"""AI summaries, embeddings, and topic-model provenance."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Summary(Base):
    """Summary attached to a work, shelf, rack, citation context set, or search result."""

    __tablename__ = "summaries"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entity_type: Mapped[str] = mapped_column(String(64), index=True)
    entity_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    summary_type: Mapped[str] = mapped_column(String(64), index=True)
    text: Mapped[str] = mapped_column(Text)
    model_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class TopicAssignment(Base):
    """Topic assignment for a work under a specific topic model scope."""

    __tablename__ = "topic_assignments"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    topic_model_id: Mapped[str] = mapped_column(String(255), index=True)
    scope_type: Mapped[str] = mapped_column(String(64), index=True)
    scope_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    work_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    topic_id: Mapped[int] = mapped_column(index=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
