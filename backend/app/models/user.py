"""User and account models."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class User(Base):
    """Authenticated user account. Guest accounts are intentionally unsupported."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(512))
    role: Mapped[str] = mapped_column(String(32), default="reader")
    # The single immutable owner (provisioned by ``make bootstrap-admin``) carries this marker.
    # It can never be disabled, deleted or role-changed, and is the only account that may manage
    # ``admin`` accounts. Exactly one row has this set to True.
    is_bootstrap: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Profile + account metadata (SPEC §9.3).
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    # Preferred Library page size (D18). NULL falls back to ``Settings.default_papers_per_page``;
    # the effective value is additionally clamped by the admin global maximum.
    papers_per_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Preferred GUI theme id (P3). NULL falls back to the boot default (``latte-warm``); the API
    # validates any set value against the bundled theme ids (see ``app.core.themes``).
    theme: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    password_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Whether this user's find-on-web downloads may use the server's Elsevier API key (UX batch 3).
    # OFF by default (NULL → False); toggled per user in Admin → Users.
    elsevier_api_allowed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
