"""Shared scope → works resolution (S1/S2; HINTS "Make scopes reusable").

Several features take a user-picked scope and operate on every paper in it (summaries, topic
models, citation graphs/summaries, visualizations, exports). This module is the ONE place that
translates a scope into works, replacing the per-feature copies whose merged-shadow + visibility
tails silently drifted apart. All SEVEN scope kinds are supported (owner decision, 2026-07-13):
the three container scopes (``library``/``shelf``/``rack``) plus the explicit-id scopes
(``search_result``/``selected_papers``/``saved_filter`` — the endpoint resolves the ids and
passes them in) and ``import_batch`` (``Work.import_batch_id``).

Design points:

* **Query-returning** (:func:`scope_works_query`): callers compose SQL-side — count without
  loading (:func:`count_scope_works`, the S15/S16 async-threshold check), paginate, or add
  filters — instead of always materializing the whole scope into Python memory.
* **``visible_ids`` is required** (S2). Every caller must state its access-control context
  explicitly: the caller's SEE-set, or ``None`` for a deliberate system context (background job
  acting on everything). Forgetting the clamp is now a ``TypeError`` at the call site instead of
  a silent data leak — the register's ``topic_graph`` IDOR was exactly that omission.
* Merged shadows (Batch D) are excluded unconditionally — they are hidden remnants of a merge and
  never part of any scope, for anyone.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ColumnElement, Select, func, select
from sqlalchemy.orm import Session

from app.models.organization import RackShelf, ShelfWork
from app.models.work import Work

SCOPE_TYPES = (
    "library",
    "shelf",
    "rack",
    "search_result",
    "selected_papers",
    "import_batch",
    "saved_filter",
)
# Scopes resolved from an explicit id list the endpoint supplies (already visibility-shaped for
# saved filters; the clamp below still applies as defense in depth).
_EXPLICIT_ID_SCOPES = ("search_result", "selected_papers", "saved_filter")


def scope_works_query(
    scope_type: str,
    scope_id: uuid.UUID | None,
    *,
    visible_ids: set[uuid.UUID] | ColumnElement | None,
    work_ids: list[uuid.UUID] | None = None,
) -> Select:
    """A ``Select[Work]`` for the scope's members, shadow-filtered and visibility-clamped.

    ``visible_ids`` may be the materialized id set OR a SQL predicate from
    ``access.visible_work_condition`` (E3) — the predicate form keeps the whole clamp inside the
    database (no giant ``IN`` list). ``None`` = deliberate system context / admin.

    Raises ``ValueError`` for an unknown scope type or a container scope without an id (callers
    map it to a 400).
    """
    if scope_type == "library":
        stmt = select(Work)
    elif scope_type in _EXPLICIT_ID_SCOPES:
        # An explicit set (search result / library selection / resolved saved filter). An empty
        # list is a valid, empty scope.
        stmt = select(Work).where(Work.id.in_(work_ids or []))
    elif scope_type == "import_batch":
        if scope_id is None:
            raise ValueError("scope id is required for an import-batch scope")
        stmt = select(Work).where(Work.import_batch_id == scope_id)
    elif scope_type == "shelf":
        if scope_id is None:
            raise ValueError("scope id is required for a shelf scope")
        stmt = (
            select(Work)
            .join(ShelfWork, ShelfWork.work_id == Work.id)
            .where(ShelfWork.shelf_id == scope_id)
        )
    elif scope_type == "rack":
        if scope_id is None:
            raise ValueError("scope id is required for a rack scope")
        stmt = (
            select(Work)
            .join(ShelfWork, ShelfWork.work_id == Work.id)
            .join(RackShelf, RackShelf.shelf_id == ShelfWork.shelf_id)
            .where(RackShelf.rack_id == scope_id)
            .distinct()
        )
    else:
        raise ValueError(f"Unsupported scope type: {scope_type!r}")
    stmt = stmt.where(Work.merged_into_id.is_(None))
    if isinstance(visible_ids, ColumnElement):
        stmt = stmt.where(visible_ids)
    elif visible_ids is not None:
        stmt = stmt.where(Work.id.in_(visible_ids))
    return stmt


def resolve_scope_works(
    db: Session,
    scope_type: str,
    scope_id: uuid.UUID | None,
    *,
    visible_ids: set[uuid.UUID] | ColumnElement | None,
    work_ids: list[uuid.UUID] | None = None,
) -> list[Work]:
    """The scope's member works, materialized (for callers that genuinely need them all)."""
    return list(
        db.scalars(
            scope_works_query(scope_type, scope_id, visible_ids=visible_ids, work_ids=work_ids)
        ).all()
    )


def count_scope_works(
    db: Session,
    scope_type: str,
    scope_id: uuid.UUID | None,
    *,
    visible_ids: set[uuid.UUID] | ColumnElement | None,
    work_ids: list[uuid.UUID] | None = None,
) -> int:
    """The scope's member count, without loading a single Work row (SQL ``COUNT`` over the query).

    This is the cheap pre-check that lets an endpoint decide "small enough to run inline" vs
    "route to the background worker" (S15/S16) before paying for materialization.
    """
    stmt = scope_works_query(scope_type, scope_id, visible_ids=visible_ids, work_ids=work_ids)
    return int(db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0)
