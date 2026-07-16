"""Shelves, racks, and tags.

Shelves group works, racks group shelves (both many-to-many, via the join tables below), and tags
attach freeform labels to any entity type via ``TagLink``. ``TagShelf``/``TagRack`` are a separate
concept: they scope *where a tag may be offered* when tagging, not what is tagged with it.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Shelf(Base):
    """Collection of works. A work can appear in multiple shelves."""

    __tablename__ = "shelves"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    # Access level (Phase H): one of ``open`` / ``visible`` / ``private``. See app.services.access.
    access_level: Mapped[str] = mapped_column(String(16), default="open")
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class Rack(Base):
    """Collection of shelves. A shelf can appear in multiple racks."""

    __tablename__ = "racks"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    # Access level (Phase H): one of ``open`` / ``visible`` / ``private``. See app.services.access.
    access_level: Mapped[str] = mapped_column(String(16), default="open")
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )


class Tag(Base):
    """Tag applicable to multiple entity types."""

    __tablename__ = "tags"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    normalized_name: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    color: Mapped[str | None] = mapped_column(String(32), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    parent_tag_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class ShelfWork(Base):
    """Membership of a work in a shelf."""

    __tablename__ = "shelf_works"

    shelf_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("shelves.id", ondelete="CASCADE"), primary_key=True
    )
    work_id: Mapped[uuid.UUID] = mapped_column(
        # index=True: the composite PK leads with shelf_id, but access-control filters query by
        # work_id alone (governing-shelf / visible-works checks on nearly every request), which the
        # PK can't serve — a standalone index backs those. (Audit: efficiency #4)
        Uuid(as_uuid=True),
        ForeignKey("works.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    )
    added_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        nullable=True,
        index=True,
    )
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    position: Mapped[int | None] = mapped_column(Integer, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class RackShelf(Base):
    """Membership of a shelf in a rack."""

    __tablename__ = "rack_shelves"

    rack_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("racks.id", ondelete="CASCADE"), primary_key=True
    )
    shelf_id: Mapped[uuid.UUID] = mapped_column(
        # index=True: rack-scope joins filter by shelf_id, which the (rack_id, shelf_id) PK can't
        # serve leading-column-wise. (Audit: efficiency #4)
        Uuid(as_uuid=True),
        ForeignKey("shelves.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    )
    added_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        nullable=True,
        index=True,
    )
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    position: Mapped[int | None] = mapped_column(Integer, nullable=True)


class TagLink(Base):
    """A tag attached to any supported entity type."""

    __tablename__ = "tag_links"

    tag_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True
    )
    entity_type: Mapped[str] = mapped_column(String(64), primary_key=True)
    entity_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


# 2026-07-16: tag scoping. A tag with NO scope rows is GLOBAL (offered everywhere). One or more
# rows restrict which shelves/racks it is OFFERED for when tagging papers there. This is distinct
# from ``TagLink`` (which means "this entity IS tagged with X"); these mean "X is AVAILABLE here".
class TagShelf(Base):
    """Restricts a tag's availability to a shelf (see note above; no rows = global)."""

    __tablename__ = "tag_shelves"

    tag_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True
    )
    shelf_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("shelves.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    )


class TagRack(Base):
    """Restricts a tag's availability to a rack (a paper qualifies via any shelf it's on in the rack)."""

    __tablename__ = "tag_racks"

    tag_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True
    )
    rack_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("racks.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    )
