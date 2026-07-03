"""Audit logging service."""

import json
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.models.audit import AuditEvent

logger = logging.getLogger(__name__)


def record_event(
    db: Session,
    event_type: str,
    *,
    actor_user_id: uuid.UUID | None = None,
    actor_agent_id: uuid.UUID | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    details: dict[str, Any] | None = None,
    settings: Settings | None = None,
) -> AuditEvent:
    """Record an audit event in the current database transaction.

    Also appends the event as one JSON line to the append-only file sink (best-effort; see
    :func:`_append_to_file_sink`) so there is a tamper-evident, DB-independent copy.
    """
    event = AuditEvent(
        id=uuid.uuid4(),
        actor_user_id=actor_user_id,
        actor_agent_id=actor_agent_id,
        event_type=event_type,
        entity_type=entity_type,
        entity_id=entity_id,
        ip_address=ip_address,
        user_agent=user_agent,
        details=details,
        created_at=datetime.now(UTC),
    )
    db.add(event)
    _append_to_file_sink(event, settings=settings)
    return event


def _append_to_file_sink(event: AuditEvent, *, settings: Settings | None = None) -> None:
    """Append ``event`` as one JSON line to the configured JSONL sink.

    Best-effort and fail-open: a missing directory is created, and any error (permissions, disk
    full, …) is swallowed with a warning so a file-write failure never breaks the request nor drops
    the durable DB row. Opening in append mode keeps concurrent writers safe.
    """
    settings = settings or get_settings()
    path = settings.audit_log_path
    if not path:
        return
    try:
        target = Path(path).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "id": str(event.id),
            "event_type": event.event_type,
            "actor_user_id": str(event.actor_user_id) if event.actor_user_id else None,
            "actor_agent_id": str(event.actor_agent_id) if event.actor_agent_id else None,
            "entity_type": event.entity_type,
            "entity_id": event.entity_id,
            "ip_address": event.ip_address,
            "user_agent": event.user_agent,
            "details": event.details,
            "created_at": (event.created_at or datetime.now(UTC)).isoformat(),
        }
        line = json.dumps(record, default=str, ensure_ascii=False)
        with target.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except Exception:  # noqa: BLE001 - the file sink is best-effort defense-in-depth
        logger.warning("audit file sink write failed for %s", event.event_type, exc_info=True)
