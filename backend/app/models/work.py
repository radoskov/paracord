"""Work and version models."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

_JSONB = JSON().with_variant(JSONB(), "postgresql")


class Work(Base):
    """Conceptual scholarly work."""

    __tablename__ = "works"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    canonical_title: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    normalized_title: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    abstract: Mapped[str | None] = mapped_column(Text, nullable=True)
    doi: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    arxiv_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    arxiv_base_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    venue: Mapped[str | None] = mapped_column(Text, nullable=True)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    work_type: Mapped[str] = mapped_column(String(64), default="unknown", index=True)
    canonical_metadata_source: Mapped[str | None] = mapped_column(String(128), nullable=True)
    reading_status: Mapped[str] = mapped_column(String(64), default="unread", index=True)
    # User-chosen primary file for one-click "Read" (#16); NULL → the first attached file is used.
    main_file_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("files.id", ondelete="SET NULL"), nullable=True
    )
    # Manual ordering within the reading queue (SPEC §8.17.1); NULL sorts last.
    queue_position: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    user_confirmed: Mapped[bool] = mapped_column(Boolean, default=False)
    # Per-field user confirmation (SPEC §8.12): names of fields the user has locked so enrichment
    # never overwrites them (e.g. ["title", "year"]). Supersedes the all-or-nothing user_confirmed.
    confirmed_fields: Mapped[list | None] = mapped_column(_JSONB, default=list)
    # Deterministic keyphrases from extraction (SPEC §8.15.1), most salient first.
    keywords: Mapped[list | None] = mapped_column(_JSONB, default=list)
    # Per-paper representative topic terms (SPEC §8.15, Phase K), most salient first. Mirrors
    # ``keywords``; populated on demand via the per-paper Topic action / topic_work_job.
    topics: Mapped[list | None] = mapped_column(_JSONB, default=list)
    # The user who created this work (Phase H access control). NULL = system/agent/import origin,
    # which is treated as a "loose" (no-owner) paper; contributor own-only edits key off this.
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), nullable=True, index=True
    )
    # The import batch that created this work (Phase B6). NULL = manually created / pre-B6 /
    # non-batch origin. FK SET NULL so deleting a batch never cascades to the paper. Backs the
    # ``import_batch`` citation-graph scope.
    import_batch_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("import_batches.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # Soft version-grouping key (Phase B6): all works linked as versions of one another share the
    # representative (canonical) work's id here. NULL = ungrouped. No FK — it is a plain grouping key
    # (a work may point at its own id). Backs graph version-collapse.
    version_group_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), nullable=True, index=True
    )
    # External citation count snapshot (Track C P1, visualization prerequisite). A cached impact
    # figure fetched during enrichment from the source with the highest priority that returned one
    # (OpenAlex > Semantic Scholar > Crossref); NULL for papers with no resolvable id. Overwritten
    # on each enrichment (newer wins), with the source it came from and when it was fetched.
    citation_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    citation_count_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    citation_count_fetched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Duplicate-merge shadow marker (Batch D). When set, this work has been merged INTO the work
    # with this id: it is a hidden "shadow" (never listed/searched/graphed/exported) and its incoming
    # references resolve to the base. NULL = a normal, visible work. FK SET NULL so deleting a base
    # never cascades a shadow away (it just becomes standalone again).
    merged_into_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("works.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Reversal record for the MOST RECENT merge that produced this shadow (Batch D single-level
    # unmerge). Captures the base fields filled, the conflict assertions added, and the ids of every
    # entity moved/redirected, so Unmerge can exactly reverse it. NULL when this is not a shadow OR
    # when the shadow has been finalized (flatten-on-re-merge makes older merges permanent). A shadow
    # is reversible iff ``merged_into_id`` is set AND ``merge_record`` is not NULL.
    merge_record: Mapped[dict | None] = mapped_column(_JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class WorkVersion(Base):
    """Specific version of a work."""

    __tablename__ = "work_versions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    work_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("works.id", ondelete="CASCADE"), index=True
    )
    version_label: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source: Mapped[str | None] = mapped_column(String(128), nullable=True)
    publication_state: Mapped[str | None] = mapped_column(String(64), nullable=True)
    version_type: Mapped[str] = mapped_column(String(64), default="unknown")
    arxiv_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    doi: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class WorkLink(Base):
    """A user-declared bidirectional relationship between two works (Batch D "Link").

    Records that two papers are related / the same work WITHOUT moving files or hiding either side
    (unlike a merge). The pair is stored order-normalized (``work_a_id`` < ``work_b_id`` as strings)
    with a unique constraint so the same relationship is never duplicated; the detail view queries
    either column to show + jump to the other paper.
    """

    __tablename__ = "work_links"
    __table_args__ = (
        UniqueConstraint("work_a_id", "work_b_id", "link_type", name="uq_work_link_pair"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    work_a_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("works.id", ondelete="CASCADE"), index=True
    )
    work_b_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("works.id", ondelete="CASCADE"), index=True
    )
    link_type: Mapped[str] = mapped_column(String(32), default="related")
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
