"""Incoming external citations — papers (from an open database) that cite a work (batch 10, #8).

Normalized so a citing paper that cites several of our works is stored **once** and referenced many
times (owner request):

* :class:`ExternalPaper` — a deduplicated "quasi-paper": metadata only, never shown in the normal
  library. Deduped by ``dedup_key`` (normalized DOI when present, else ``source:external_id``).
* :class:`ExternalCitationLink` — "this external paper cites that local work" (many-to-many).

Distinct from ``Reference`` (the *outgoing* direction: this work cites X). Fetched on demand from
OpenAlex (falling back to Semantic Scholar), stored permanently, refetchable to update.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ExternalPaper(Base):
    """A deduplicated external paper (metadata only) — not a library work."""

    __tablename__ = "external_papers"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Stable identity used to dedup across fetches: normalized DOI if present, else source:external_id.
    dedup_key: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    # Provenance of the first fetch that created this row ("openalex" / "semanticscholar").
    source: Mapped[str] = mapped_column(String(32))
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    doi: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    arxiv_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # The library work this citing paper IS, when the local matcher recognizes it (identifier match
    # or a fuzzy match passing the reference-matching gates). Lets the UI mark in-library citers and
    # the graph link them; NULL = external-only. Maintained by fetch, the rescan job, merge, delete.
    resolved_work_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("works.id", ondelete="SET NULL"), nullable=True, index=True
    )
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Authors as a "; "-joined display string (matches the authors metadata convention).
    authors: Mapped[str | None] = mapped_column(Text, nullable=True)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    venue: Mapped[str | None] = mapped_column(Text, nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class ExternalCitationLink(Base):
    """An external paper cites a local work (incoming citation edge)."""

    __tablename__ = "external_citation_links"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    external_paper_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("external_papers.id", ondelete="CASCADE"), index=True
    )
    work_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("works.id", ondelete="CASCADE"), index=True
    )
    # When this citing relationship was last (re)fetched for the work (drives the "as of" display).
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    __table_args__ = (
        UniqueConstraint("external_paper_id", "work_id", name="uq_external_citation_link"),
    )
