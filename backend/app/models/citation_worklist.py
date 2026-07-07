"""Per-user decisions on frequently-cited-but-missing works (Track C C3a).

The citation-summary "frequently cited but missing" block is an acquisition to-do: for each missing
work (keyed by its normalized identifier/title — see ``citation_summary._missing_key``) the user can
record a decision — ``import`` (queued to acquire) or ``ignore`` (drop from the active list). The
decision is keyed by that stable ``missing_key`` (not a reference id), so it survives a summary
recompute and re-extraction. Decisions are per-user (a few LAN users may each keep their own list).
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class MissingWorkDecision(Base):
    """A user's ``import``/``ignore`` decision for one frequently-cited-but-missing work."""

    __tablename__ = "missing_work_decisions"
    __table_args__ = (
        UniqueConstraint("user_id", "missing_key", name="uq_missing_decision_user_key"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # The normalized missing-work key (``doi:…`` / ``arxiv:…`` / ``title:…``) from citation_summary.
    missing_key: Mapped[str] = mapped_column(String(512), nullable=False)
    # 'import' (queued to acquire) | 'ignore' (hidden from the active list).
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
