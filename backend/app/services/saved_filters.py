"""Saved-filter resolution + per-user CRUD helpers (Phase B7).

A :class:`SavedFilter` resolves to a set of Work ids by feeding its stored query into
``build_works_query`` — which starts from ``access.visible_works_query`` — so the resolved set is
ALWAYS clamped to what the running actor may see and can never widen (a filter saved by one user
and run by a lower-visibility user yields only the intersection). Semantic ranking is client-side,
so a ``saved_filter`` scope resolves on the STRUCTURED params + query operators only (a scope is a
set, not a ranked list); the ``search_mode`` is preserved for the Library "apply" flow but ignored
during scope resolution.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.saved_filter import SavedFilter
from app.models.user import User
from app.models.work import Work
from app.services.works_query import build_works_query


def resolve_saved_filter_work_ids(
    db: Session, actor: User, saved_filter: SavedFilter
) -> list[uuid.UUID]:
    """Resolve a saved filter to the Work ids it matches, clamped to ``actor``'s visible set.

    Unpacks the stored ``query_text`` + structured ``params`` and runs them through
    ``build_works_query`` (which floors on ``access.visible_works_query``). The semantic mode is not
    server-resolvable, so this resolves on the structured params/operators only (see module docs).
    """
    params = saved_filter.params or {}
    missing = params.get("missing") or []
    stmt = build_works_query(
        db,
        actor,
        q=saved_filter.query_text,
        reading_status=params.get("reading_status"),
        shelf_id=_as_uuid(params.get("shelf_id")),
        rack_id=_as_uuid(params.get("rack_id")),
        row_id=_as_uuid(params.get("row_id")),
        tag_id=_as_uuid(params.get("tag_id")),
        has_pdf=params.get("has_pdf"),
        has_references=params.get("has_references"),
        missing=",".join(missing) if isinstance(missing, list) else missing,
    )
    return list(db.scalars(stmt.with_only_columns(Work.id)).all())


def _as_uuid(value: object) -> uuid.UUID | None:
    """Coerce a stored id (str/UUID/None) into a UUID, tolerating bad data."""
    if value is None or isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        return None


# --------------------------------------------------------------------------------------------------
# Per-user CRUD helpers (all ownership checks filter on ``owner_user_id == actor.id``).
# --------------------------------------------------------------------------------------------------
def list_saved_filters(db: Session, actor: User) -> list[SavedFilter]:
    """Return the caller's saved filters, ordered by name."""
    return list(
        db.scalars(
            select(SavedFilter)
            .where(SavedFilter.owner_user_id == actor.id)
            .order_by(SavedFilter.name)
        ).all()
    )


def get_owned_saved_filter(db: Session, actor: User, filter_id: uuid.UUID) -> SavedFilter | None:
    """Return the caller's saved filter by id, or ``None`` (never another user's — 404, not 403)."""
    return db.scalar(
        select(SavedFilter).where(
            SavedFilter.id == filter_id, SavedFilter.owner_user_id == actor.id
        )
    )
