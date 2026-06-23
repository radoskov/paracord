"""Audit logging service."""

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.models.audit import AuditEvent


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
) -> AuditEvent:
    """Record an audit event in the current database transaction."""
    event = AuditEvent(
        actor_user_id=actor_user_id,
        actor_agent_id=actor_agent_id,
        event_type=event_type,
        entity_type=entity_type,
        entity_id=entity_id,
        ip_address=ip_address,
        user_agent=user_agent,
        details=details,
    )
    db.add(event)
    return event
