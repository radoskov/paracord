"""Shared membership color-groups for graph color-by (shelf / rack / tag).

Papers live on several shelves, in several racks (via those shelves), and carry several tags —
so a single "color group" per node under-represents them. Every graph surface (Insights citation
graph, the paper-view reference graph, the Visualizations views) resolves memberships through
THIS module so the privacy rule stays in one place: only non-private (open/visible) shelves and
racks may surface as group names — a private shelf/rack name never leaks as a node color.

The frontend renders single-membership nodes as plain circles and multi-membership nodes as a
small SVG pie ("color wheel"), one segment per group.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from typing import TYPE_CHECKING

from sqlalchemy import ColumnElement, or_, select
from sqlalchemy.orm import Session

from app.models.organization import (
    Rack,
    RackShelf,
    Row,
    RowRack,
    Shelf,
    ShelfWork,
    Tag,
    TagLink,
)

if TYPE_CHECKING:
    from app.models.user import User

# Mirrors ``access._OPEN_OR_VISIBLE`` (and citation_graph's shelf rule).
NON_PRIVATE_LEVELS = ("open", "visible")

# The color-by kinds this module resolves; other kinds (status/year/topic/…) read directly off
# the work in each service.
MEMBERSHIP_COLOR_KINDS = ("shelf", "rack", "row", "tag")

# Default group when a paper has no (visible) membership of the requested kind.
EMPTY_GROUP = {"shelf": "unshelved", "rack": "unracked", "row": "unrowed", "tag": "untagged"}


def _visibility_condition(
    db: Session, model: type[Shelf] | type[Rack] | type[Row], target_type: str, actor: User | None
) -> ColumnElement[bool] | None:
    """Access filter limiting which shelves/racks/rows may surface as a color-group NAME to ``actor``.

    Mirrors ``access.visible_racks_query`` / ``visible_shelves_query`` / ``visible_rows_query`` so a
    paper is coloured by the same shelves/racks/rows the viewer could see anywhere else:

    * ``actor is None`` → conservative fallback: only non-private names (a private shelf/rack name
      never leaks to an unknown viewer). Trusted internal callers that want everything pass an
      admin/owner ``actor``.
    * admin/owner ``actor`` → no restriction (sees all, including their own **private** racks — the
      bug this fixes: an owner's private rack used to collapse every paper to "unracked").
    * other ``actor`` → non-private OR explicitly granted (group grant).
    """
    from app.services import access

    if actor is not None and access.is_admin_or_owner(actor):
        return None
    cond: ColumnElement[bool] = model.access_level.in_(NON_PRIVATE_LEVELS)
    if actor is not None:
        granted = access.granted_target_ids(db, actor, target_type)
        if granted:
            cond = or_(cond, model.id.in_(granted))
    return cond


def membership_groups(
    db: Session,
    work_ids: Iterable[uuid.UUID],
    color_by: str,
    actor: User | None = None,
) -> dict[uuid.UUID, list[str]]:
    """ALL (access-filtered, sorted) membership names per work for a membership color-by kind.

    Every work id in the input appears in the result — works without a visible membership map to
    ``[EMPTY_GROUP[color_by]]`` so they form their own legend group instead of vanishing.

    ``actor`` scopes which shelf/rack NAMES may surface (see :func:`_visibility_condition`): an
    admin/owner sees all of theirs (incl. private), so their own private racks colour normally;
    ``None`` keeps the conservative non-private-only fallback so a missing viewer never leaks a
    private name.
    """
    ids = list(dict.fromkeys(work_ids))
    if color_by not in MEMBERSHIP_COLOR_KINDS:
        raise ValueError(f"Not a membership color-by kind: {color_by}")
    if not ids:
        return {}

    if color_by == "shelf":
        shelf_cond = _visibility_condition(db, Shelf, "shelf", actor)
        stmt = (
            select(ShelfWork.work_id, Shelf.name)
            .join(Shelf, Shelf.id == ShelfWork.shelf_id)
            .where(ShelfWork.work_id.in_(ids))
        )
        if shelf_cond is not None:
            stmt = stmt.where(shelf_cond)
        rows = db.execute(stmt).all()
    elif color_by == "rack":
        # A paper's racks are the racks of its shelves; the rack name surfaces only if the viewer
        # may see BOTH the shelf providing the path and the rack itself.
        shelf_cond = _visibility_condition(db, Shelf, "shelf", actor)
        rack_cond = _visibility_condition(db, Rack, "rack", actor)
        stmt = (
            select(ShelfWork.work_id, Rack.name)
            .join(Shelf, Shelf.id == ShelfWork.shelf_id)
            .join(RackShelf, RackShelf.shelf_id == Shelf.id)
            .join(Rack, Rack.id == RackShelf.rack_id)
            .where(ShelfWork.work_id.in_(ids))
        )
        if shelf_cond is not None:
            stmt = stmt.where(shelf_cond)
        if rack_cond is not None:
            stmt = stmt.where(rack_cond)
        rows = db.execute(stmt).all()
    elif color_by == "row":
        # A paper's rows are the rows of its shelves' racks (work→shelf→rack→row); the row name
        # surfaces only if the viewer may see the shelf, the rack AND the row on that path.
        shelf_cond = _visibility_condition(db, Shelf, "shelf", actor)
        rack_cond = _visibility_condition(db, Rack, "rack", actor)
        row_cond = _visibility_condition(db, Row, "row", actor)
        stmt = (
            select(ShelfWork.work_id, Row.name)
            .join(Shelf, Shelf.id == ShelfWork.shelf_id)
            .join(RackShelf, RackShelf.shelf_id == Shelf.id)
            .join(Rack, Rack.id == RackShelf.rack_id)
            .join(RowRack, RowRack.rack_id == Rack.id)
            .join(Row, Row.id == RowRack.row_id)
            .where(ShelfWork.work_id.in_(ids))
        )
        if shelf_cond is not None:
            stmt = stmt.where(shelf_cond)
        if rack_cond is not None:
            stmt = stmt.where(rack_cond)
        if row_cond is not None:
            stmt = stmt.where(row_cond)
        rows = db.execute(stmt).all()
    else:  # tag
        rows = db.execute(
            select(TagLink.entity_id, Tag.name)
            .join(Tag, Tag.id == TagLink.tag_id)
            .where(TagLink.entity_type == "work", TagLink.entity_id.in_(ids))
        ).all()

    grouped: dict[uuid.UUID, set[str]] = {}
    for work_id, name in rows:
        if name:
            grouped.setdefault(work_id, set()).add(name)
    empty = EMPTY_GROUP[color_by]
    return {wid: sorted(grouped[wid]) if wid in grouped else [empty] for wid in ids}
