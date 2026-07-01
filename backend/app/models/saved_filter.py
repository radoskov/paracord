"""Per-user saved library filter (Phase B7).

A :class:`SavedFilter` is a named snapshot of a Library query: a free-text ``query_text`` (with
structured search operators), a ``search_mode`` (``metadata``/``semantic``), and a structured
``params`` blob (reading status, shelf/rack/tag ids, has-pdf/has-references flags, and a list of
missing-field names). Filters are owned per-user and can be applied in the Library or used as a
graph/export scope. Resolution always goes through ``access.visible_works_query`` so a saved
filter can never widen visibility beyond what the running user may already see.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text, UniqueConstraint, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# A JSON column that becomes JSONB on Postgres and plain JSON on SQLite (mirrors the other models).
_JSONB = JSON().with_variant(JSONB(), "postgresql")


class SavedFilter(Base):
    """A user-owned, named Library query usable as a filter and as a graph/export scope."""

    __tablename__ = "saved_filters"
    __table_args__ = (UniqueConstraint("owner_user_id", "name", name="uq_saved_filter_owner_name"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # 'metadata' | 'semantic' (semantic ranking is client-side; a saved_filter scope resolves on the
    # structured params only — see services.saved_filters).
    search_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="metadata")
    query_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    # {reading_status, shelf_id, rack_id, tag_id, has_pdf, has_references, missing: [...]}.
    params: Mapped[dict] = mapped_column(_JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
