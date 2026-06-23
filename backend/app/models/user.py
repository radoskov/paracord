"""User and account models."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class User(Base):
    """Authenticated user account. Guest accounts are intentionally unsupported."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(512))
    role: Mapped[str] = mapped_column(String(32), default="reader")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
