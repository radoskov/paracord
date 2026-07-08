"""Local workstation agent + enrollment models (SPEC §11.2).

Enrollment is owner-gated: an owner mints a short-lived enrollment token, the agent presents it
to request enrollment (creating a ``pending`` agent), and an owner approves it — only then is a
scoped agent access token issued. Tokens are stored hashed, never in plaintext.
"""

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


class Agent(Base):
    """A local workstation agent enrolled against this server."""

    __tablename__ = "agents"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    token_hash: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), nullable=True, index=True
    )
    # Identity + lifecycle metadata (SPEC §9.3).
    host_alias: Mapped[str | None] = mapped_column(String(255), nullable=True)
    capabilities: Mapped[dict | None] = mapped_column(_JSONB, nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), nullable=True, index=True
    )

    # Per-agent privileges (SPEC §32.8). Least-privilege for permanent storage (teleport off);
    # indexing, transient extract, being-requested, and visibility are on by default.
    can_index: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    can_extract: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    can_teleport: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    can_be_requested: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    processing_visibility: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true"
    )
    server_status_visibility: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true"
    )


class AgentEnrollmentToken(Base):
    """A single-use, owner-issued token an agent presents to request enrollment."""

    __tablename__ = "agent_enrollment_tokens"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    token_hash: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_by_agent_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), nullable=True, index=True
    )
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentFile(Base):
    """A file an agent has indexed on its workstation and reported via a manifest.

    The server only ever stores opaque identity (``local_file_id``, hash, size) and a
    display-only path label — never a server-usable filesystem path. Teleport is an
    agent-push: the user requests it (``teleport_status='requested'``), the agent uploads
    the bytes, and the server verifies the hash before storing the managed file.
    """

    __tablename__ = "agent_files"
    __table_args__ = (UniqueConstraint("agent_id", "local_file_id", name="uq_agent_local_file"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), index=True
    )
    local_file_id: Mapped[str] = mapped_column(String(255), index=True)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    display_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # Stable human-readable reference (path relative to monitored root); resolvable to the real
    # on-disk path only by the local agent (SPEC §32.4).
    virtual_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Intended import action for this file: index_only | index_and_extract | teleport (§32.4).
    import_action: Mapped[str] = mapped_column(String(32), default="index_only")
    # Teleport request policy for this file: ask | allow (§32.6).
    teleport_policy: Mapped[str] = mapped_column(String(16), default="ask")
    # Overall disposition: indexed | extracting | extracted | teleported | failed | source_removed.
    processing_state: Mapped[str] = mapped_column(String(32), default="indexed", index=True)
    # Reject-forever block: the agent auto-rejects all future teleport requests for this file.
    teleport_blocked: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # Lightweight server-side preview kept for index_and_extract (PDF itself is discarded).
    preview_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    teleport_status: Mapped[str] = mapped_column(String(32), default="none", index=True)
    file_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("files.id", ondelete="SET NULL"), nullable=True
    )
    # The library "stub" paper an ``index_only`` entry created (B6). Kept so a re-scan doesn't
    # duplicate the stub and a later extract/teleport enriches this same work. SET NULL on delete.
    work_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("works.id", ondelete="SET NULL"), nullable=True, index=True
    )
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
