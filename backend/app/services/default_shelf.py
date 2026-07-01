"""The ephemeral "default shelf" — so no paper is ever free-floating (#1).

Every paper must sit on at least one shelf (before this, a paper on no shelf was silently treated
as world-visible). Newly added papers land on a single default shelf whose access level the admin
controls; the moment a paper is filed onto any *other* shelf it leaves the default shelf (ephemeral
membership), and if it is later removed from its last real shelf it falls back onto the default
shelf again. The default shelf is a normal shelf (id stored on the access-settings singleton), so
its visibility is managed through the usual shelf access-level controls.

All helpers are system operations (no per-actor ACL) — they maintain a global invariant — and none
commit; the caller owns the transaction boundary.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, inspect, select
from sqlalchemy.orm import Session

from app.models.access_settings import ACCESS_SETTINGS_SINGLETON_ID, AccessSettings
from app.models.organization import Shelf, ShelfWork
from app.services.access_settings import get_default_access_level

DEFAULT_SHELF_NAME = "Inbox"

# Memo of whether the organization tables exist for a bind (narrow unit-test schemas omit them).
_TABLES_PRESENT: dict[int, bool] = {}


def _tables_present(db: Session) -> bool:
    """Narrow unit schemas create only a few tables; the default-shelf invariant then no-ops."""
    key = id(db.get_bind())
    if key not in _TABLES_PRESENT:
        inspector = inspect(db.connection())
        _TABLES_PRESENT[key] = all(
            inspector.has_table(t) for t in ("shelves", "shelf_works", "access_settings")
        )
    return _TABLES_PRESENT[key]


def get_default_shelf_id(db: Session) -> uuid.UUID | None:
    """The configured default shelf id, or None if not bootstrapped yet."""
    if not _tables_present(db):
        return None
    row = db.get(AccessSettings, ACCESS_SETTINGS_SINGLETON_ID)
    return row.default_shelf_id if row is not None else None


def get_or_create_default_shelf(db: Session, *, actor_id: uuid.UUID | None = None) -> Shelf | None:
    """Return the default shelf, creating it (at the global default access level) if absent.

    The shelf id is assigned client-side so no intermediate ``flush`` is needed — the caller's
    commit persists everything at once (an interleaved flush corrupts the shared StaticPool
    connection used in tests). Returns None when the org tables are absent (narrow unit schemas)."""
    if not _tables_present(db):
        return None
    row = db.get(AccessSettings, ACCESS_SETTINGS_SINGLETON_ID)
    if row is not None and row.default_shelf_id is not None:
        shelf = db.get(Shelf, row.default_shelf_id)
        if shelf is not None:
            return shelf
    shelf = Shelf(
        id=uuid.uuid4(),
        name=DEFAULT_SHELF_NAME,
        access_level=get_default_access_level(db),
        created_by_user_id=actor_id,
    )
    db.add(shelf)
    if row is None:
        db.add(AccessSettings(id=ACCESS_SETTINGS_SINGLETON_ID, default_shelf_id=shelf.id))
    else:
        row.default_shelf_id = shelf.id
    return shelf


def _shelf_count(db: Session, work_id: uuid.UUID) -> int:
    return int(
        db.scalar(select(func.count()).select_from(ShelfWork).where(ShelfWork.work_id == work_id))
        or 0
    )


def remove_from_default(db: Session, work_id: uuid.UUID) -> None:
    """Drop a work from the default shelf (called when it is filed onto a real shelf)."""
    default_id = get_default_shelf_id(db)
    if default_id is None:
        return
    link = db.get(ShelfWork, {"shelf_id": default_id, "work_id": work_id})
    if link is not None:
        db.delete(link)


def place_on_default_if_loose(
    db: Session, work_id: uuid.UUID, *, actor_id: uuid.UUID | None = None
) -> None:
    """Ensure a work is on at least one shelf; if loose, add it to the default shelf."""
    if not _tables_present(db) or _shelf_count(db, work_id) > 0:
        return
    shelf = get_or_create_default_shelf(db, actor_id=actor_id)
    if shelf is not None and db.get(ShelfWork, {"shelf_id": shelf.id, "work_id": work_id}) is None:
        db.add(ShelfWork(shelf_id=shelf.id, work_id=work_id, added_by_user_id=actor_id))
