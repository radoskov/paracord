"""Shared endpoint-side scope validation (Insights audit S-a, 2026-07-13).

Every analysis endpoint (citation graph, topic graph, visualization, citation summary,
venue/author summary, missing-works export) used to copy the same three steps: SEE-check the
scope container (404), require an id for a ``saved_filter`` scope (400), and expand the caller's
own saved filter to its visibility-clamped work ids (404 on a missing/foreign filter). Six
near-identical copies drifted style-wise; this is the one implementation.
"""

from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.user import User
from app.services import access
from app.services.saved_filters import get_owned_saved_filter, resolve_saved_filter_work_ids


def resolve_scope_or_404(
    db: Session,
    actor: User,
    *,
    scope_type: str,
    scope_id: uuid.UUID | None,
    work_ids: list[uuid.UUID] | None = None,
) -> list[uuid.UUID] | None:
    """Validate a scope for ``actor`` and return the effective explicit work-id list.

    * 404 when the container (shelf/rack) isn't SEE-able — indistinguishable from not existing;
    * 400 when a ``saved_filter`` scope arrives without an id;
    * 404 when the saved filter is missing or owned by someone else;
    * ``saved_filter`` expands to its resolved (already visibility-clamped) work ids.

    Returns ``work_ids`` (possibly the expanded filter ids) — ``None`` for container scopes,
    which the services resolve themselves via ``scope_resolution``.
    """
    if not access.can_see_scope_container(db, actor, scope_type=scope_type, scope_id=scope_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scope not found")
    if scope_type != "saved_filter":
        return work_ids
    if scope_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="scope id is required")
    saved = get_owned_saved_filter(db, actor, scope_id)
    if saved is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Scope not found")
    return resolve_saved_filter_work_ids(db, actor, saved)
