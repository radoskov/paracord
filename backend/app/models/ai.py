"""AI summaries, embeddings, and topic-model provenance."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Float, Integer, String, Text, UniqueConstraint, Uuid
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
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class Embedding(Base):
    """A dense vector embedding for an entity (stored as JSON for portability).

    Vectors are kept as a plain JSON array rather than a pgvector column so the same code path
    works on SQLite (tests) and Postgres; nearest-neighbour ranking is done in Python. A
    pgvector index is a future scaling optimization for large libraries.
    """

    __tablename__ = "embeddings"
    __table_args__ = (
        UniqueConstraint(
            "entity_type", "entity_id", "model_name", name="uq_embedding_entity_model"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    entity_type: Mapped[str] = mapped_column(String(64), index=True)
    entity_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    model_name: Mapped[str] = mapped_column(String(255), index=True)
    dim: Mapped[int] = mapped_column(Integer)
    vector: Mapped[list[Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


# Single-row primary key so there is at most one AI-config row (a settings singleton).
AI_CONFIG_SINGLETON_ID = uuid.UUID(int=1)


class AIConfig(Base):
    """Owner-managed runtime AI provider configuration (overlays the static ``Settings`` defaults).

    A single row (id == :data:`AI_CONFIG_SINGLETON_ID`). Any column left ``NULL`` falls back to the
    corresponding ``Settings`` default, so an empty/absent row reproduces the out-of-the-box
    lexical-baseline behavior. Edited from the Admin "AI & Models" panel (WORKPLAN_NEXT Stage 8),
    never from a config file at runtime.
    """

    __tablename__ = "ai_config"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=AI_CONFIG_SINGLETON_ID
    )
    embedding_provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    summary_provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    summary_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    topic_backend: Mapped[str | None] = mapped_column(String(64), nullable=True)
    topic_embedding_model: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # OCR / advanced-extraction backend (Phase B5): none | ocrmypdf | pymupdf. NULL → Settings.
    ocr_backend: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # OCR languages in tesseract syntax (e.g. "eng" or "eng+spa"). NULL → Settings.ocr_language.
    ocr_language: Mapped[str | None] = mapped_column(String(128), nullable=True)
    ollama_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)


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
