"""Passage-level chunks of a work, for chunk-level semantic search (HYBRID-SEARCH-DESIGN §3).

A work is split into section-aware passages; each passage is embedded (chunk-level dense retrieval)
so search can surface the *relevant passage*, not just an on-topic paper. Per the existing
``embeddings.vector_pg`` pattern, the per-model pgvector columns are **not** declared on this ORM
model — they are Postgres-only, added by a best-effort migration and read/written via raw SQL — so
the model stays dialect-agnostic and works unchanged under the SQLite test path.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class WorkChunk(Base):
    """A passage of a work (title / abstract / a body section), for semantic retrieval."""

    __tablename__ = "work_chunks"
    __table_args__ = (UniqueConstraint("work_id", "position", name="uq_work_chunk_position"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    work_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("works.id", ondelete="CASCADE"), index=True
    )
    # Source section label ("title", "abstract", or a TEI section head like "Methods"); NULL when
    # unknown. Kept so the semantic side can show the matching passage's section and so a later
    # per-chunk section weight (Arch B) would be possible.
    section: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # 0-based order of this chunk within its work (stable; backs the unique constraint).
    position: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    # Approximate token count (whitespace words) — used for chunk sizing + display.
    token_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
