"""Reference and citation-context models."""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

# A JSON column that becomes JSONB on Postgres (for @>/-> queries) and plain JSON on SQLite.
_JSONB = JSON().with_variant(JSONB(), "postgresql")


class Reference(Base):
    """A **canonical** bibliographic reference (the cited thing).

    A single row is shared by every citing work that references the same cited paper (batch 12): the
    per-work citation edges live in :class:`ReferenceCitation`, and the in-text mentions/section
    weights stay per-citing-work in :class:`CitationMention`. Deduplicated by :attr:`dedup_key`
    (normalized DOI → arXiv base → ``title:<normalized_title>|<year>``).

    ``resolved_work_id`` is the confirmed local work this reference IS (set by identifier match, a
    user-confirmed fuzzy match, or the manual import/merge paths). ``suggested_work_id`` +
    ``match_score`` carry an *unconfirmed* fuzzy "likely local" candidate — never promoted to
    ``resolved_work_id`` until confirmed, so a guess can't corrupt the ref→ref edges/metrics that
    read ``resolved_work_id``.
    """

    __tablename__ = "references"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    resolved_work_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("works.id", ondelete="SET NULL"), nullable=True, index=True
    )
    suggested_work_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("works.id", ondelete="SET NULL"), nullable=True, index=True
    )
    match_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    raw_citation: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Normalized title (``normalize_title``) — the fuzzy blocking key + part of the dedup key.
    normalized_title: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    doi: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    arxiv_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Parsed author display names (list of strings). Persisted from the TEI so author-overlap
    # matching + display are possible; NULL for pre-batch-12 rows until re-extraction.
    authors: Mapped[list[str] | None] = mapped_column(_JSONB, nullable=True)
    # Stable identity for dedup/consolidation. NOT unique at the DB level: the structural migration
    # keeps pre-existing duplicates as distinct rows (same key) until the consolidation job merges
    # them; going forward, extraction find-or-creates by this key.
    dedup_key: Mapped[str | None] = mapped_column(String(512), nullable=True, index=True)
    resolution_status: Mapped[str] = mapped_column(String(32), default="unresolved", index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    citations: Mapped[list["ReferenceCitation"]] = relationship(
        back_populates="reference", cascade="all, delete-orphan"
    )


class ReferenceCitation(Base):
    """A per-citing-work edge onto a (shared) canonical :class:`Reference` (batch 12).

    Replaces the old single ``Reference.citing_work_id`` FK: one link row per (reference, citing
    work), so one canonical reference can be cited by many works.
    """

    __tablename__ = "reference_citations"
    __table_args__ = (
        UniqueConstraint("reference_id", "citing_work_id", name="uq_reference_citation"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    reference_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("references.id", ondelete="CASCADE"), index=True
    )
    citing_work_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("works.id", ondelete="CASCADE"), index=True
    )
    source_tei_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("raw_tei_documents.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    reference: Mapped["Reference"] = relationship(back_populates="citations")


class CitationMention(Base):
    """Specific in-text citation mention and its context."""

    __tablename__ = "citation_mentions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    citing_work_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("works.id", ondelete="CASCADE"), index=True
    )
    reference_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("references.id", ondelete="CASCADE"), index=True
    )
    resolved_cited_work_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("works.id", ondelete="SET NULL"), nullable=True, index=True
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
        Uuid(as_uuid=True),
        ForeignKey("raw_tei_documents.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
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
