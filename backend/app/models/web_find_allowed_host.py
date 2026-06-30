"""GUI-managed find-on-web allowed download hosts (batch 2 #5 hardening).

Owner/admin-managed allowlist of additional hosts that find-on-web may download a PDF from,
stored in the database and **merged** with the built-in default allowlist in
:mod:`app.services.web_find` (the defaults are never written to). Each row pins one ``host``
pattern (exact host, parent-domain suffix, or a ``*.`` subdomain wildcard). The denylist always
wins over the allowlist, so adding a shadow-library host here can never enable a download.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class WebFindAllowedHost(Base):
    """A GUI-added find-on-web allowed download host pattern."""

    __tablename__ = "web_find_allowed_hosts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    host: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
