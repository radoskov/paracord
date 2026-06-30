"""GUI-managed server import roots (batch 2 #19).

Owner-managed whitelist of server-local folders that the "Server folder" import may scan, stored in
the database and **merged** with the read-only ``storage.server_allowed_roots`` entries from
``server.yaml`` (the YAML entries are never written to). Each row pins an absolute ``path`` to a
unique ``alias``; the same path-containment / existing-directory validation that protects the YAML
roots applies identically to these.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ImportRoot(Base):
    """A GUI-added server-folder import root (alias → absolute path)."""

    __tablename__ = "import_roots"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    alias: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    path: Mapped[str] = mapped_column(Text)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
