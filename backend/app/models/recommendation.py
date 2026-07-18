"""Cached AI categorization/tag recommendation runs (feature: Insights → Recommend categorization).

A run is cached per (scope + settings + model) so a dropped connection / tab refresh doesn't force a
full recompute (a run over a large scope can take minutes to hours). Mirrors the ``Summary``
provenance shape. The heavy per-paper suggestions + raw LLM in/out live in the ``result`` JSON blob.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# Sentinel scope_id for the whole-library scope (which has no container id), matching the summaries
# convention so a library run has a concrete cache key.
LIBRARY_SCOPE_SENTINEL = uuid.UUID(int=0)


class RecommendationRun(Base):
    """One computed recommendation run over a scope, cached for reuse until recomputed."""

    __tablename__ = "recommendation_runs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    scope_type: Mapped[str] = mapped_column(String(32), index=True)
    # LIBRARY_SCOPE_SENTINEL for the library scope; otherwise the shelf/rack/row/batch/filter id, or
    # a stable hash of an explicit work-id set (search_result / selected_papers).
    scope_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), index=True)
    mode: Mapped[str] = mapped_column(String(32), index=True)  # "tags" | "categorization"
    # Stable hash of the settings that affect the output (mode, k, scoring, parent_combine,
    # prefilter, cap) — the cache key together with scope + model.
    params_hash: Mapped[str] = mapped_column(String(64), index=True)
    params: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    provider_used: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # True when the run degraded (affinity→ranking, or no-LLM→embedding) — surfaced in the UI.
    fallback: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="running")  # running | done | failed
    error: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # The per-paper suggestions + raw LLM input/output (for the result popups). Tens of kB–a few MB.
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
