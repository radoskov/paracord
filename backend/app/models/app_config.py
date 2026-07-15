"""Owner-managed runtime application configuration (D18).

A DB-backed settings singleton (mirrors :mod:`app.models.ai`'s ``AIConfig``) holding app-wide knobs
an owner/admin edits at runtime rather than via a config file. Currently just the global maximum
Library page size. A single row (id == :data:`APP_CONFIG_SINGLETON_ID`); an absent row means the
static ``Settings`` defaults apply.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# Single-row primary key so there is at most one app-config row (a settings singleton).
APP_CONFIG_SINGLETON_ID = uuid.UUID(int=1)

# Out-of-the-box global ceiling on the Library page size; mirrors ``Settings.max_papers_per_page``.
_DEFAULT_MAX_PAPERS_PER_PAGE = 500

# Out-of-the-box overload-protection defaults (D1). Rate limits are per rolling minute; a request
# exceeding either the per-client or the global ceiling is rejected with 429.
_DEFAULT_RATE_LIMIT_PER_CLIENT_PER_MIN = 120
_DEFAULT_RATE_LIMIT_GLOBAL_PER_MIN = 600

# Out-of-the-box ceiling on how many items a single client import batch may carry (D1). Server-folder
# scans (a local scan, not a client batch) are exempt from this cap.
_DEFAULT_MAX_BATCH_ITEMS = 100

# Out-of-the-box number of RQ extraction worker processes the supervisor launches (D1). Read once at
# worker-container start; changing it requires a worker restart to apply.
_DEFAULT_RQ_WORKER_COUNT = 2

# Out-of-the-box ceiling on how many jobs may be pending in the RQ queue at once (D39). A
# job-creating request is rejected with 429 when the pending depth is already at this cap; the
# measurement fails open (allows) when Redis is unreachable.
_DEFAULT_MAX_QUEUE_LEN = 1000

# Out-of-the-box cap on how many citing papers one fetch stores per paper (S20). Providers are
# paged up to this cap; changeable in Admin → Settings (egress-volume knob).
_DEFAULT_CITING_PAPERS_FETCH_CAP = 1000

# Out-of-the-box scope size above which topic-model/summary requests run as a background job
# instead of inline in the request (S15/S16).
_DEFAULT_AI_SCOPE_JOB_THRESHOLD = 100

# Out-of-the-box cap on items per citation-summary column (UX batch). Generous by default — the
# UI folds each column into a scrollable window, so a large cap surfaces every significant entry
# without hiding the tail (the old fixed 15 hid important items).
_DEFAULT_CITATION_SUMMARY_ITEM_CAP = 100

# Out-of-the-box per-surface node caps for the analysis graphs (Insights audit L-a). High and
# admin-editable; a capped graph keeps its highest-degree nodes and reports the hidden count.
_DEFAULT_CITATION_GRAPH_NODE_CAP = 1500
_DEFAULT_TOPIC_GRAPH_NODE_CAP = 400
_DEFAULT_VIZ_NODE_CAP = 500


class AppConfig(Base):
    """Owner-managed runtime application configuration (overlays the static ``Settings`` defaults).

    A single row (id == :data:`APP_CONFIG_SINGLETON_ID`). Edited from the Admin panel, never from a
    config file at runtime. An absent row reproduces the out-of-the-box ``Settings`` behaviour.
    """

    __tablename__ = "app_config"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=APP_CONFIG_SINGLETON_ID
    )
    # Global clamp on the Library page size (D18). Server default mirrors
    # ``Settings.max_papers_per_page`` so a freshly-inserted row keeps the built-in ceiling.
    max_papers_per_page: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=str(_DEFAULT_MAX_PAPERS_PER_PAGE)
    )
    # Overload protection (D1): shared Redis rate-limit ceilings per rolling minute.
    rate_limit_per_client_per_min: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=str(_DEFAULT_RATE_LIMIT_PER_CLIENT_PER_MIN)
    )
    rate_limit_global_per_min: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=str(_DEFAULT_RATE_LIMIT_GLOBAL_PER_MIN)
    )
    # Overload protection (D1): max items in a single client import batch (server scans are exempt).
    max_batch_items: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=str(_DEFAULT_MAX_BATCH_ITEMS)
    )
    # Overload protection (D1): RQ worker processes the supervisor launches (apply-on-restart).
    rq_worker_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=str(_DEFAULT_RQ_WORKER_COUNT)
    )
    # Overload protection (D39): ceiling on the pending RQ queue depth. A job-creating request is
    # rejected with 429 once the pending queue is at this cap (fail-open if Redis is unreachable).
    max_queue_len: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=str(_DEFAULT_MAX_QUEUE_LEN)
    )
    # Citing-papers fetch cap (S20): max external citers fetched+cached per paper (paged fetch).
    citing_papers_fetch_cap: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=str(_DEFAULT_CITING_PAPERS_FETCH_CAP)
    )
    # AI scope-job threshold (S15/S16): scopes larger than this run topics/summaries on the worker.
    ai_scope_job_threshold: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=str(_DEFAULT_AI_SCOPE_JOB_THRESHOLD)
    )
    # Items per citation-summary column (UX batch): how many entries each ranked block returns.
    citation_summary_item_cap: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=str(_DEFAULT_CITATION_SUMMARY_ITEM_CAP)
    )
    # Per-surface analysis node caps (L-a).
    citation_graph_node_cap: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=str(_DEFAULT_CITATION_GRAPH_NODE_CAP)
    )
    topic_graph_node_cap: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=str(_DEFAULT_TOPIC_GRAPH_NODE_CAP)
    )
    viz_node_cap: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=str(_DEFAULT_VIZ_NODE_CAP)
    )
    # Elsevier Article Retrieval API key (UX batch 3, owner-set in Admin → Find-on-web). NULL →
    # fall back to the yaml/env default. Write-only through the admin API (reads report only
    # whether a key is configured).
    elsevier_api_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Master switch for using the stored Elsevier key at all (NULL → True). The key can stay
    # stored while its use is disabled; per-user allowance (Users tab) additionally gates it.
    elsevier_api_enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # Reference→library matching (batch 12, owner item #1). When ON, a fuzzy "likely local" candidate
    # that clears the title threshold + gates becomes a HARD link (``resolved_work_id`` set, counted in
    # every graph/metric calculation) instead of a soft one-click suggestion. OFF (default; an absent
    # row or NULL → False) keeps fuzzy candidates as suggestions. The numeric matcher params live in
    # the static ``Settings``/YAML; only this toggle needs runtime edits, hence its home here.
    use_fuzzy_match_as_confirmed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # Fuzzy auto-accept threshold (UX batch): the score at/above which the toggle above hard-links
    # a fuzzy match. NULL → the yaml default (``reference_matching.auto_accept_threshold``); always
    # clamped to at least ``reference_matching.min_auto_accept_threshold`` (yaml-only floor).
    fuzzy_accept_threshold: Mapped[float | None] = mapped_column(Float, nullable=True)
    # High-confidence auto-accept (UX batch): when ON (default; NULL → True), a fuzzy match at/above
    # the yaml-only ``reference_matching.high_confidence_threshold`` (default 100 = exact normalized
    # title) is hard-linked even without a DOI/arXiv id — independent of the fuzzy toggle above.
    use_high_confidence_auto_accept: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # Reference→library matching (F3a): when ON, the API enqueues a full library-wide reference
    # rematch on startup (best-effort, like the D7 sweep) so the stored reference→work resolution
    # stays fresh across deploys. OFF by default (an absent row or a NULL value → False).
    reference_rescan_on_startup: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
    updated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
