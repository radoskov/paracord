"""User-group and access-grant models (Phase H access control).

Linux-like groups: every user belongs to one auto-managed **personal group** (named == their
username) plus any number of shared groups an admin/owner creates. A :class:`GroupGrant` attaches
a group to a ``rack`` or ``shelf`` target, conferring SEE (and, for librarians, MODIFY) access to
that target under the visible/private access levels. :class:`DefaultGrant` rows are the grant set
applied to every newly created personal group, so a fresh user starts with a sensible baseline.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# Allowed grant/target entity types.
GRANT_TARGET_TYPES = ("rack", "shelf", "row")


class Group(Base):
    """A named collection of users. Personal groups mirror a single user 1:1."""

    __tablename__ = "groups"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    # True for the auto-managed per-user personal group (created on user create, deleted on user
    # delete). Personal groups cannot be deleted directly via the admin API.
    is_personal: Mapped[bool] = mapped_column(default=False, nullable=False)
    # The owning user for a personal group (NULL for shared groups). Cascades on user delete.
    personal_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
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


class GroupMembership(Base):
    """Membership of a user in a group (M2M)."""

    __tablename__ = "group_memberships"

    group_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
        index=True,
    )
    # No FK: audit-only reference to the granting user, kept even if that user is later deleted.
    added_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class GroupGrant(Base):
    """A group's access grant to one rack or shelf target."""

    __tablename__ = "group_grants"
    __table_args__ = (
        UniqueConstraint("group_id", "target_type", "target_id", name="uq_group_grant_target"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    group_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("groups.id", ondelete="CASCADE"), index=True
    )
    # ``rack`` or ``shelf`` (see ``GRANT_TARGET_TYPES``).
    target_type: Mapped[str] = mapped_column(String(16))
    target_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True))
    added_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class DefaultGrant(Base):
    """A target that every newly created personal group is granted (admin-configurable)."""

    __tablename__ = "default_grants"
    __table_args__ = (UniqueConstraint("target_type", "target_id", name="uq_default_grant_target"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    target_type: Mapped[str] = mapped_column(String(16))
    target_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True))
    added_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
