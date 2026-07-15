"""Effective application configuration (D18).

Resolves runtime app-wide knobs by overlaying the owner-editable ``app_config`` DB row on the static
``Settings`` defaults (DB wins; an absent row reproduces the out-of-the-box behaviour). Currently
just the global maximum Library page size. Uses the same table-presence guard as
:mod:`app.services.ai_config` so narrow unit-test schemas that omit the table don't break, and a read
never rolls back the caller's transaction.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models.app_config import (
    _DEFAULT_AI_SCOPE_JOB_THRESHOLD,
    _DEFAULT_CITATION_GRAPH_NODE_CAP,
    _DEFAULT_CITATION_SUMMARY_ITEM_CAP,
    _DEFAULT_CITING_PAPERS_FETCH_CAP,
    _DEFAULT_MAX_BATCH_ITEMS,
    _DEFAULT_MAX_QUEUE_LEN,
    _DEFAULT_RATE_LIMIT_GLOBAL_PER_MIN,
    _DEFAULT_RATE_LIMIT_PER_CLIENT_PER_MIN,
    _DEFAULT_RQ_WORKER_COUNT,
    _DEFAULT_TOPIC_GRAPH_NODE_CAP,
    _DEFAULT_VIZ_NODE_CAP,
    APP_CONFIG_SINGLETON_ID,
    AppConfig,
)
from app.utils.table_presence import table_present


class BatchTooLargeError(Exception):
    """A client import batch exceeded the configured ``max_batch_items`` cap (D1)."""

    def __init__(self, *, limit: int, count: int) -> None:
        self.limit = limit
        self.count = count
        super().__init__(
            f"Batch of {count} exceeds the {limit}-item limit; split it into smaller imports"
        )


def _app_config_table_present(db: Session) -> bool:
    """Whether the ``app_config`` table exists (narrow unit-test schemas omit it)."""
    return table_present(db, AppConfig.__tablename__)


def effective_max_papers_per_page(db: Session, *, settings: Settings | None = None) -> int:
    """Return the effective global maximum Library page size (DB row value, else Settings default)."""
    settings = settings or get_settings()
    if not _app_config_table_present(db):
        return settings.max_papers_per_page
    row = db.get(AppConfig, APP_CONFIG_SINGLETON_ID)
    if row is None or row.max_papers_per_page is None:
        return settings.max_papers_per_page
    return row.max_papers_per_page


def effective_rate_limit_per_client_per_min(
    db: Session, *, settings: Settings | None = None
) -> int:
    """Return the effective per-client request rate-limit ceiling (requests per rolling minute)."""
    if not _app_config_table_present(db):
        return _DEFAULT_RATE_LIMIT_PER_CLIENT_PER_MIN
    row = db.get(AppConfig, APP_CONFIG_SINGLETON_ID)
    if row is None or row.rate_limit_per_client_per_min is None:
        return _DEFAULT_RATE_LIMIT_PER_CLIENT_PER_MIN
    return row.rate_limit_per_client_per_min


def effective_rate_limit_global_per_min(db: Session, *, settings: Settings | None = None) -> int:
    """Return the effective global request rate-limit ceiling (requests per rolling minute)."""
    if not _app_config_table_present(db):
        return _DEFAULT_RATE_LIMIT_GLOBAL_PER_MIN
    row = db.get(AppConfig, APP_CONFIG_SINGLETON_ID)
    if row is None or row.rate_limit_global_per_min is None:
        return _DEFAULT_RATE_LIMIT_GLOBAL_PER_MIN
    return row.rate_limit_global_per_min


def effective_max_batch_items(db: Session, *, settings: Settings | None = None) -> int:
    """Return the effective cap on items in a single client import batch (server scans exempt)."""
    if not _app_config_table_present(db):
        return _DEFAULT_MAX_BATCH_ITEMS
    row = db.get(AppConfig, APP_CONFIG_SINGLETON_ID)
    if row is None or row.max_batch_items is None:
        return _DEFAULT_MAX_BATCH_ITEMS
    return row.max_batch_items


def enforce_batch_limit(db: Session, count: int) -> None:
    """Raise :class:`BatchTooLargeError` when ``count`` exceeds the configured batch-item cap."""
    limit = effective_max_batch_items(db)
    if count > limit:
        raise BatchTooLargeError(limit=limit, count=count)


def effective_rq_worker_count(db: Session, *, settings: Settings | None = None) -> int:
    """Return the effective number of RQ worker processes (read once by the supervisor at start)."""
    if not _app_config_table_present(db):
        return _DEFAULT_RQ_WORKER_COUNT
    row = db.get(AppConfig, APP_CONFIG_SINGLETON_ID)
    if row is None or row.rq_worker_count is None:
        return _DEFAULT_RQ_WORKER_COUNT
    return row.rq_worker_count


def effective_max_queue_len(db: Session, *, settings: Settings | None = None) -> int:
    """Return the effective ceiling on the pending RQ queue depth (D39)."""
    if not _app_config_table_present(db):
        return _DEFAULT_MAX_QUEUE_LEN
    row = db.get(AppConfig, APP_CONFIG_SINGLETON_ID)
    if row is None or row.max_queue_len is None:
        return _DEFAULT_MAX_QUEUE_LEN
    return row.max_queue_len


def effective_citation_summary_item_cap(db: Session, *, settings: Settings | None = None) -> int:
    """Return the effective per-column item cap for the citation summary (UX batch)."""
    if not _app_config_table_present(db):
        return _DEFAULT_CITATION_SUMMARY_ITEM_CAP
    row = db.get(AppConfig, APP_CONFIG_SINGLETON_ID)
    if row is None or row.citation_summary_item_cap is None:
        return _DEFAULT_CITATION_SUMMARY_ITEM_CAP
    return row.citation_summary_item_cap


def update_citation_summary_item_cap(
    db: Session, *, value: int, actor_user_id: uuid.UUID | None = None
) -> int:
    """Persist a new citation-summary per-column item cap (UX batch)."""
    return _update_int(db, "citation_summary_item_cap", value, actor_user_id)


def effective_elsevier_api_key(db: Session, *, settings: Settings | None = None) -> str | None:
    """The Elsevier Article Retrieval API key: admin-set value, else the yaml/env default."""
    settings = settings or get_settings()
    fallback = (getattr(settings, "web_find_elsevier_api_key", None) or "").strip() or None
    if not _app_config_table_present(db):
        return fallback
    row = db.get(AppConfig, APP_CONFIG_SINGLETON_ID)
    if row is None or not (row.elsevier_api_key or "").strip():
        return fallback
    return row.elsevier_api_key.strip()


def update_elsevier_api_key(
    db: Session, *, value: str | None, actor_user_id: uuid.UUID | None = None
) -> bool:
    """Persist (or clear, on empty/None) the Elsevier API key. Returns whether a key is now set."""
    row = _ensure_row(db)
    row.elsevier_api_key = (value or "").strip() or None
    row.updated_by_user_id = actor_user_id
    db.flush()
    return row.elsevier_api_key is not None


def effective_citing_papers_fetch_cap(db: Session, *, settings: Settings | None = None) -> int:
    """Return the effective cap on citing papers fetched+cached per paper (S20)."""
    if not _app_config_table_present(db):
        return _DEFAULT_CITING_PAPERS_FETCH_CAP
    row = db.get(AppConfig, APP_CONFIG_SINGLETON_ID)
    if row is None or row.citing_papers_fetch_cap is None:
        return _DEFAULT_CITING_PAPERS_FETCH_CAP
    return row.citing_papers_fetch_cap


def update_citing_papers_fetch_cap(
    db: Session, *, value: int, actor_user_id: uuid.UUID | None = None
) -> int:
    """Persist a new citing-papers fetch cap (S20). Returns the stored value."""
    if value < 1:
        raise ValueError("citing_papers_fetch_cap must be >= 1")
    row = _ensure_row(db)
    row.citing_papers_fetch_cap = value
    row.updated_by_user_id = actor_user_id
    db.flush()
    return row.citing_papers_fetch_cap


def effective_ai_scope_job_threshold(db: Session, *, settings: Settings | None = None) -> int:
    """Return the scope size above which topic/summary requests run as a background job (S15/S16)."""
    if not _app_config_table_present(db):
        return _DEFAULT_AI_SCOPE_JOB_THRESHOLD
    row = db.get(AppConfig, APP_CONFIG_SINGLETON_ID)
    if row is None or row.ai_scope_job_threshold is None:
        return _DEFAULT_AI_SCOPE_JOB_THRESHOLD
    return row.ai_scope_job_threshold


def update_ai_scope_job_threshold(
    db: Session, *, value: int, actor_user_id: uuid.UUID | None = None
) -> int:
    """Persist a new AI scope-job threshold (S15/S16). Returns the stored value."""
    if value < 1:
        raise ValueError("ai_scope_job_threshold must be >= 1")
    row = _ensure_row(db)
    row.ai_scope_job_threshold = value
    row.updated_by_user_id = actor_user_id
    db.flush()
    return row.ai_scope_job_threshold


def _effective_int(db: Session, field: str, default: int) -> int:
    if not _app_config_table_present(db):
        return default
    row = db.get(AppConfig, APP_CONFIG_SINGLETON_ID)
    value = getattr(row, field, None) if row is not None else None
    return default if value is None else value


def _update_int(db: Session, field: str, value: int, actor_user_id: uuid.UUID | None) -> int:
    if value < 1:
        raise ValueError(f"{field} must be >= 1")
    row = _ensure_row(db)
    setattr(row, field, value)
    row.updated_by_user_id = actor_user_id
    db.flush()
    return getattr(row, field)


def effective_citation_graph_node_cap(db: Session) -> int:
    """Max nodes the citation graph keeps (highest-degree first; hidden count reported). L-a."""
    return _effective_int(db, "citation_graph_node_cap", _DEFAULT_CITATION_GRAPH_NODE_CAP)


def effective_topic_graph_node_cap(db: Session) -> int:
    """Max works the topic-similarity graph embeds/keeps (L-a)."""
    return _effective_int(db, "topic_graph_node_cap", _DEFAULT_TOPIC_GRAPH_NODE_CAP)


def effective_viz_node_cap(db: Session) -> int:
    """Default max nodes for the visualization views (request may lower it). L-a."""
    return _effective_int(db, "viz_node_cap", _DEFAULT_VIZ_NODE_CAP)


def update_citation_graph_node_cap(
    db: Session, *, value: int, actor_user_id: uuid.UUID | None = None
) -> int:
    """Persist a new citation-graph node cap (L-a)."""
    return _update_int(db, "citation_graph_node_cap", value, actor_user_id)


def update_topic_graph_node_cap(
    db: Session, *, value: int, actor_user_id: uuid.UUID | None = None
) -> int:
    """Persist a new topic-graph node cap (L-a)."""
    return _update_int(db, "topic_graph_node_cap", value, actor_user_id)


def update_viz_node_cap(db: Session, *, value: int, actor_user_id: uuid.UUID | None = None) -> int:
    """Persist a new visualization node cap (L-a)."""
    return _update_int(db, "viz_node_cap", value, actor_user_id)


def effective_use_fuzzy_match_as_confirmed(
    db: Session, *, settings: Settings | None = None
) -> bool:
    """Whether a fuzzy "likely local" match is auto-promoted to a hard link (batch 12, owner #1).

    OFF by default: a fuzzy candidate stays a soft ``likely_match`` suggestion. An absent app_config
    row or a NULL column reproduces that default.
    """
    if not _app_config_table_present(db):
        return False
    row = db.get(AppConfig, APP_CONFIG_SINGLETON_ID)
    if row is None or row.use_fuzzy_match_as_confirmed is None:
        return False
    return bool(row.use_fuzzy_match_as_confirmed)


def update_use_fuzzy_match_as_confirmed(
    db: Session, *, value: bool, actor_user_id: uuid.UUID | None = None
) -> bool:
    """Persist the fuzzy-as-confirmed toggle (batch 12). Returns the stored value."""
    row = _ensure_row(db)
    row.use_fuzzy_match_as_confirmed = bool(value)
    row.updated_by_user_id = actor_user_id
    db.flush()
    return bool(row.use_fuzzy_match_as_confirmed)


def effective_fuzzy_accept_threshold(db: Session, *, settings: Settings | None = None) -> float:
    """The similarity_pct at/above which fuzzy auto-accept hard-links a match (UX batch).

    NULL / absent row → the yaml default (``reference_matching.auto_accept_threshold``). Always
    clamped into [``min_auto_accept_threshold``, 100] — the floor is yaml-only, so admins can
    never open the gate below it (e.g. an accidental 0%).
    """
    settings = settings or get_settings()
    floor = float(settings.reference_matching_min_auto_accept_threshold)
    value = float(settings.reference_matching_auto_accept_threshold)
    if _app_config_table_present(db):
        row = db.get(AppConfig, APP_CONFIG_SINGLETON_ID)
        if row is not None and row.fuzzy_accept_threshold is not None:
            value = float(row.fuzzy_accept_threshold)
    return min(100.0, max(floor, value))


def update_fuzzy_accept_threshold(
    db: Session,
    *,
    value: float,
    actor_user_id: uuid.UUID | None = None,
    settings: Settings | None = None,
) -> float:
    """Persist the admin-set fuzzy auto-accept threshold. Rejects values below the yaml floor."""
    settings = settings or get_settings()
    floor = float(settings.reference_matching_min_auto_accept_threshold)
    if not floor <= float(value) <= 100.0:
        raise ValueError(
            f"Fuzzy auto-accept threshold must be between {floor:g} and 100 "
            f"(the minimum is set in server.yaml)"
        )
    row = _ensure_row(db)
    row.fuzzy_accept_threshold = float(value)
    row.updated_by_user_id = actor_user_id
    db.flush()
    return float(row.fuzzy_accept_threshold)


def effective_use_high_confidence_auto_accept(
    db: Session, *, settings: Settings | None = None
) -> bool:
    """Whether a fuzzy match at/above the yaml-only high-confidence threshold (default 100 = exact
    normalized title) is hard-linked even without a DOI/arXiv id. ON by default (NULL → True)."""
    if not _app_config_table_present(db):
        return True
    row = db.get(AppConfig, APP_CONFIG_SINGLETON_ID)
    if row is None or row.use_high_confidence_auto_accept is None:
        return True
    return bool(row.use_high_confidence_auto_accept)


def update_use_high_confidence_auto_accept(
    db: Session, *, value: bool, actor_user_id: uuid.UUID | None = None
) -> bool:
    """Persist the high-confidence auto-accept toggle (UX batch). Returns the stored value."""
    row = _ensure_row(db)
    row.use_high_confidence_auto_accept = bool(value)
    row.updated_by_user_id = actor_user_id
    db.flush()
    return bool(row.use_high_confidence_auto_accept)


def effective_accept_policy(db: Session, *, settings: Settings | None = None):
    """The full fuzzy-match acceptance policy (UX batch): runtime toggles + clamped threshold
    overlaid on the yaml bounds. Returns a :class:`app.services.reference_matching.AcceptPolicy`."""
    from app.services.reference_matching import AcceptPolicy

    settings = settings or get_settings()
    return AcceptPolicy(
        use_fuzzy=effective_use_fuzzy_match_as_confirmed(db),
        fuzzy_threshold=effective_fuzzy_accept_threshold(db, settings=settings),
        use_high_confidence=effective_use_high_confidence_auto_accept(db),
        high_confidence_threshold=float(settings.reference_matching_high_confidence_threshold),
    )


def effective_reference_rescan_on_startup(db: Session, *, settings: Settings | None = None) -> bool:
    """Whether the API enqueues a full reference rematch on startup (F3a). Default OFF.

    An absent app_config row or a NULL column reproduces the OFF default.
    """
    if not _app_config_table_present(db):
        return False
    row = db.get(AppConfig, APP_CONFIG_SINGLETON_ID)
    if row is None or row.reference_rescan_on_startup is None:
        return False
    return bool(row.reference_rescan_on_startup)


def update_reference_rescan_on_startup(
    db: Session, *, value: bool, actor_user_id: uuid.UUID | None = None
) -> bool:
    """Persist the reference-rescan-on-startup toggle (F3a). Returns the stored value."""
    row = _ensure_row(db)
    row.reference_rescan_on_startup = bool(value)
    row.updated_by_user_id = actor_user_id
    db.flush()
    return bool(row.reference_rescan_on_startup)


def _ensure_row(db: Session) -> AppConfig:
    row = db.get(AppConfig, APP_CONFIG_SINGLETON_ID)
    if row is None:
        row = AppConfig(id=APP_CONFIG_SINGLETON_ID)
        db.add(row)
    return row


def update_max_papers_per_page(
    db: Session, *, value: int, actor_user_id: uuid.UUID | None = None
) -> int:
    """Persist a new global maximum Library page size. Returns the stored value."""
    if value < 1:
        raise ValueError("max_papers_per_page must be >= 1")
    row = _ensure_row(db)
    row.max_papers_per_page = value
    row.updated_by_user_id = actor_user_id
    db.flush()
    return row.max_papers_per_page


def update_rate_limits(
    db: Session,
    *,
    per_client_per_min: int | None = None,
    global_per_min: int | None = None,
    actor_user_id: uuid.UUID | None = None,
) -> None:
    """Persist new request rate-limit ceilings (only the provided fields are changed)."""
    if per_client_per_min is not None and per_client_per_min < 1:
        raise ValueError("rate_limit_per_client_per_min must be >= 1")
    if global_per_min is not None and global_per_min < 1:
        raise ValueError("rate_limit_global_per_min must be >= 1")
    row = _ensure_row(db)
    if per_client_per_min is not None:
        row.rate_limit_per_client_per_min = per_client_per_min
    if global_per_min is not None:
        row.rate_limit_global_per_min = global_per_min
    row.updated_by_user_id = actor_user_id
    db.flush()


def update_max_batch_items(
    db: Session, *, value: int, actor_user_id: uuid.UUID | None = None
) -> int:
    """Persist a new client import-batch item cap. Returns the stored value."""
    if value < 1:
        raise ValueError("max_batch_items must be >= 1")
    row = _ensure_row(db)
    row.max_batch_items = value
    row.updated_by_user_id = actor_user_id
    db.flush()
    return row.max_batch_items


def update_rq_worker_count(
    db: Session, *, value: int, actor_user_id: uuid.UUID | None = None
) -> int:
    """Persist a new RQ worker-process count (applied on the next worker restart). Returns it."""
    if value < 1:
        raise ValueError("rq_worker_count must be >= 1")
    row = _ensure_row(db)
    row.rq_worker_count = value
    row.updated_by_user_id = actor_user_id
    db.flush()
    return row.rq_worker_count


def update_max_queue_len(db: Session, *, value: int, actor_user_id: uuid.UUID | None = None) -> int:
    """Persist a new pending-queue depth ceiling (D39). Returns the stored value."""
    if value < 1:
        raise ValueError("max_queue_len must be >= 1")
    row = _ensure_row(db)
    row.max_queue_len = value
    row.updated_by_user_id = actor_user_id
    db.flush()
    return row.max_queue_len
