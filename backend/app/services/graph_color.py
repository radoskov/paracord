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

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.organization import Rack, RackShelf, Shelf, ShelfWork, Tag, TagLink

# Mirrors ``access._OPEN_OR_VISIBLE`` (and citation_graph's shelf rule).
NON_PRIVATE_LEVELS = ("open", "visible")

# The color-by kinds this module resolves; other kinds (status/year/topic/…) read directly off
# the work in each service.
MEMBERSHIP_COLOR_KINDS = ("shelf", "rack", "tag")

# Default group when a paper has no (visible) membership of the requested kind.
EMPTY_GROUP = {"shelf": "unshelved", "rack": "unracked", "tag": "untagged"}


def membership_groups(
    db: Session, work_ids: Iterable[uuid.UUID], color_by: str
) -> dict[uuid.UUID, list[str]]:
    """ALL (privacy-filtered, sorted) membership names per work for a membership color-by kind.

    Every work id in the input appears in the result — works without a visible membership map to
    ``[EMPTY_GROUP[color_by]]`` so they form their own legend group instead of vanishing.
    """
    ids = list(dict.fromkeys(work_ids))
    if color_by not in MEMBERSHIP_COLOR_KINDS:
        raise ValueError(f"Not a membership color-by kind: {color_by}")
    if not ids:
        return {}

    if color_by == "shelf":
        rows = db.execute(
            select(ShelfWork.work_id, Shelf.name)
            .join(Shelf, Shelf.id == ShelfWork.shelf_id)
            .where(
                ShelfWork.work_id.in_(ids),
                Shelf.access_level.in_(NON_PRIVATE_LEVELS),
            )
        ).all()
    elif color_by == "rack":
        # A paper's racks are the racks of its shelves; both the shelf and the rack must be
        # non-private for the rack name to surface.
        rows = db.execute(
            select(ShelfWork.work_id, Rack.name)
            .join(Shelf, Shelf.id == ShelfWork.shelf_id)
            .join(RackShelf, RackShelf.shelf_id == Shelf.id)
            .join(Rack, Rack.id == RackShelf.rack_id)
            .where(
                ShelfWork.work_id.in_(ids),
                Shelf.access_level.in_(NON_PRIVATE_LEVELS),
                Rack.access_level.in_(NON_PRIVATE_LEVELS),
            )
        ).all()
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
