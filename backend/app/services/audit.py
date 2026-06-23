"""Audit logging service."""

from typing import Any


def record_event(event_type: str, *, details: dict[str, Any] | None = None) -> None:
    """Record an audit event.

    TODO: Persist audit events to database and include actor/IP/session context.
    """
    _ = (event_type, details)
