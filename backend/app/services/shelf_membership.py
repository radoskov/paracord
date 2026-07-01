"""Shared, ACL-checked shelf-membership helper (Phase J item 6).

A single place where "add this paper to this shelf" lives, so every import path (identifier,
bibtex/ris/csl, batch, the manual shelf endpoint) inherits the SAME access check. The rule:

  * the shelf must exist (else 404),
  * the actor must be able to modify it (``access.can_modify_shelf``, else 403),
  * then a ``ShelfWork`` row is upserted (idempotent — re-adding updates position/note).

The helper raises :class:`fastapi.HTTPException` so callers in the request path get the right
status without restating the rule. It does NOT commit — the caller owns the transaction boundary.
"""

from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models.organization import Shelf, ShelfWork
from app.models.user import User
from app.services import access


def add_work_to_shelf_checked(
    db: Session,
    *,
    shelf_id: uuid.UUID,
    work_id: uuid.UUID,
    actor: User,
    settings: Settings | None = None,
    position: int | None = None,
    note: str | None = None,
) -> ShelfWork:
    """Add ``work_id`` to ``shelf_id`` after checking the actor may modify the shelf.

    Returns the (created or existing) :class:`ShelfWork` link. Raises 404 if the shelf is missing
    and 403 if the actor lacks modify access. ``settings`` is accepted for signature symmetry with
    the other import helpers; the ACL itself needs only the db + actor + shelf.
    """
    _ = settings  # currently unused; kept so all import-to-shelf calls share one signature
    shelf = db.get(Shelf, shelf_id)
    if shelf is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shelf not found")
    if not access.can_modify_shelf(db, actor, shelf):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to modify this shelf",
        )
    link = db.get(ShelfWork, {"shelf_id": shelf_id, "work_id": work_id})
    if link is None:
        link = ShelfWork(
            shelf_id=shelf_id,
            work_id=work_id,
            added_by_user_id=actor.id,
            position=position,
            note=note,
        )
        db.add(link)
    else:
        if position is not None:
            link.position = position
        if note is not None:
            link.note = note
    # Ephemeral default-shelf membership (#1): filing a paper onto any real shelf removes it from
    # the default shelf, so the default shelf only ever holds papers with no other home.
    from app.services.default_shelf import (  # noqa: PLC0415
        get_default_shelf_id,
        remove_from_default,
    )

    if shelf_id != get_default_shelf_id(db):
        remove_from_default(db, work_id)
    return link
