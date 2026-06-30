"""Access-control permission layer (Phase H).

Read-only helpers that decide who may SEE and who may MODIFY racks, shelves and papers, plus
SQLAlchemy query builders that FILTER list endpoints to only the visible rows. Endpoints should
FILTER list/collection reads (never just 403 the whole list) and guard single-object reads/mutates.

Roles (linear ladder, see ``app.core.security.Role``):
    reader < contributor < editor < librarian < admin < owner

Access levels on a rack/shelf (``access_level`` column):
    * ``open``     — everyone may SEE; librarian+ may MODIFY by role alone.
    * ``visible``  — everyone may SEE; MODIFY needs librarian+ AND a group grant.
    * ``private``  — SEE needs a group grant (admin/owner always); MODIFY needs librarian+ AND grant.

**Admin/owner bypass everything** (both SEE and MODIFY), everywhere.

Paper (Work) governance — THE MULTI-SHELF RULE:
    A paper's access is decided by the **most-permissive governing shelf** (the shelves it belongs
    to via ShelfWork). This governs BOTH SEE and MODIFY:
      * A paper in **no shelf** is "loose" and treated as **open** (visible + modifiable by role).
      * SEE: the paper is see-able if ANY governing shelf is see-able by the user.
      * MODIFY: a paper is modifiable if SEE holds AND ANY governing shelf permits the modify
        (open → role alone; visible/private → role AND a grant on that shelf). The single most
        permissive shelf decides — a grant on one shelf is enough even if other shelves are private
        without a grant.
    Role layering on top of the shelf decision:
      * reader        — SEE only.
      * contributor   — SEE + MODIFY **own** papers only (``Work.created_by_user_id == user.id``).
      * editor+       — SEE + MODIFY **any** see-able paper (subject to the shelf modify-rule).
      * admin/owner   — everything.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Select, exists, inspect, or_, select
from sqlalchemy.orm import Session

from app.core.security import Role, role_at_least
from app.models.group import GroupGrant, GroupMembership
from app.models.organization import Rack, Shelf, ShelfWork
from app.models.user import User
from app.models.work import Work

# Access levels for which everyone can SEE without a grant.
_OPEN_OR_VISIBLE = ("open", "visible")

# Per-engine memo of whether the Phase H group tables exist. Narrow service-level unit-test schemas
# create only the tables they need and may omit ``group_memberships`` / ``group_grants``; the grant
# lookups must then behave as "no groups, no grants" rather than erroring. Mirrors the resilient
# probe in ``app.services.web_find_settings``.
_GROUP_TABLES_PRESENT: dict[int, bool] = {}


def _group_tables_present(db: Session) -> bool:
    bind = db.get_bind()
    key = id(bind)
    if key not in _GROUP_TABLES_PRESENT:
        # Reflect on the session's live connection (see groups._groups_table_present rationale).
        inspector = inspect(db.connection())
        _GROUP_TABLES_PRESENT[key] = inspector.has_table(
            GroupMembership.__tablename__
        ) and inspector.has_table(GroupGrant.__tablename__)
    return _GROUP_TABLES_PRESENT[key]


def is_admin_or_owner(user: User) -> bool:
    """True if the user bypasses all content ACLs (admin or owner)."""
    return role_at_least(user.role, Role.ADMIN)


# --------------------------------------------------------------------------------------------------
# Group / grant lookups
# --------------------------------------------------------------------------------------------------
def user_group_ids(db: Session, user: User) -> set[uuid.UUID]:
    """Return the set of group ids the user belongs to (empty if the group tables are absent)."""
    if not _group_tables_present(db):
        return set()
    return set(
        db.scalars(select(GroupMembership.group_id).where(GroupMembership.user_id == user.id)).all()
    )


def granted_target_ids(db: Session, user: User, target_type: str) -> set[uuid.UUID]:
    """Return the set of ``target_type`` (``rack``/``shelf``) ids the user has a grant for.

    A user "has a grant" for a target if any group they belong to has a :class:`GroupGrant` to it.
    """
    group_ids = user_group_ids(db, user)
    if not group_ids:
        return set()
    return set(
        db.scalars(
            select(GroupGrant.target_id).where(
                GroupGrant.group_id.in_(group_ids),
                GroupGrant.target_type == target_type,
            )
        ).all()
    )


# --------------------------------------------------------------------------------------------------
# Rack / shelf SEE + MODIFY
# --------------------------------------------------------------------------------------------------
def _can_see_target(
    db: Session, user: User, *, access_level: str, target_id: uuid.UUID, target_type: str
) -> bool:
    if is_admin_or_owner(user):
        return True
    if (access_level or "open") in _OPEN_OR_VISIBLE:
        return True
    # private -> needs a grant
    return target_id in granted_target_ids(db, user, target_type)


def _can_modify_target(
    db: Session, user: User, *, access_level: str, target_id: uuid.UUID, target_type: str
) -> bool:
    if is_admin_or_owner(user):
        return True
    # Structure changes need librarian+.
    if not role_at_least(user.role, Role.LIBRARIAN):
        return False
    level = access_level or "open"
    if level == "open":
        return True
    # visible/private -> librarian AND a group grant ("not even a librarian without a grant").
    return target_id in granted_target_ids(db, user, target_type)


def can_see_rack(db: Session, user: User, rack: Rack) -> bool:
    """True if the user may see this rack."""
    return _can_see_target(
        db, user, access_level=rack.access_level, target_id=rack.id, target_type="rack"
    )


def can_see_shelf(db: Session, user: User, shelf: Shelf) -> bool:
    """True if the user may see this shelf."""
    return _can_see_target(
        db, user, access_level=shelf.access_level, target_id=shelf.id, target_type="shelf"
    )


def can_modify_rack(db: Session, user: User, rack: Rack) -> bool:
    """True if the user may modify this rack's structure/metadata."""
    return _can_modify_target(
        db, user, access_level=rack.access_level, target_id=rack.id, target_type="rack"
    )


def can_modify_shelf(db: Session, user: User, shelf: Shelf) -> bool:
    """True if the user may modify this shelf's structure/metadata/membership."""
    return _can_modify_target(
        db, user, access_level=shelf.access_level, target_id=shelf.id, target_type="shelf"
    )


# --------------------------------------------------------------------------------------------------
# Paper (Work) SEE + MODIFY — most-permissive governing shelf, loose = open
# --------------------------------------------------------------------------------------------------
def _governing_shelves(db: Session, work_id: uuid.UUID) -> list[Shelf]:
    """Return the shelves that contain this work (its governing shelves)."""
    return list(
        db.scalars(
            select(Shelf)
            .join(ShelfWork, ShelfWork.shelf_id == Shelf.id)
            .where(ShelfWork.work_id == work_id)
        ).all()
    )


def can_see_work(db: Session, user: User, work: Work) -> bool:
    """True if the user may see this paper (most-permissive governing shelf; loose = open)."""
    if is_admin_or_owner(user):
        return True
    shelves = _governing_shelves(db, work.id)
    if not shelves:
        return True  # loose paper -> open
    return any(can_see_shelf(db, user, shelf) for shelf in shelves)


def can_modify_work(db: Session, user: User, work: Work) -> bool:
    """True if the user may modify this paper.

    Requires SEE plus a modify-permitting governing shelf (open -> role; visible/private ->
    role + grant); a loose paper (no shelf) is treated as open. Contributors may only modify their
    OWN papers (``created_by_user_id``); editor+ may modify any see-able paper.
    """
    if is_admin_or_owner(user):
        return True
    # Need at least contributor to modify any paper.
    if not role_at_least(user.role, Role.CONTRIBUTOR):
        return False
    # Contributor: own papers only.
    if not role_at_least(user.role, Role.EDITOR) and work.created_by_user_id != user.id:
        return False
    # Must be able to see it.
    if not can_see_work(db, user, work):
        return False
    shelves = _governing_shelves(db, work.id)
    if not shelves:
        return True  # loose paper -> open
    # Most-permissive shelf: any shelf that permits a content modify is enough. Content (paper)
    # edits do NOT require the librarian structure floor — an editor may edit a paper on an open
    # shelf. visible/private shelves additionally require a group grant.
    for shelf in shelves:
        level = shelf.access_level or "open"
        if level == "open":
            return True
        if shelf.id in granted_target_ids(db, user, "shelf"):
            return True
    return False


# --------------------------------------------------------------------------------------------------
# FILTER query builders for list endpoints
# --------------------------------------------------------------------------------------------------
def visible_racks_query(db: Session, user: User) -> Select:
    """Return a ``select(Rack)`` filtered to the racks the user may see."""
    stmt = select(Rack)
    if is_admin_or_owner(user):
        return stmt
    granted = granted_target_ids(db, user, "rack")
    cond = Rack.access_level.in_(_OPEN_OR_VISIBLE)
    if granted:
        cond = or_(cond, Rack.id.in_(granted))
    return stmt.where(cond)


def visible_shelves_query(db: Session, user: User) -> Select:
    """Return a ``select(Shelf)`` filtered to the shelves the user may see."""
    stmt = select(Shelf)
    if is_admin_or_owner(user):
        return stmt
    granted = granted_target_ids(db, user, "shelf")
    cond = Shelf.access_level.in_(_OPEN_OR_VISIBLE)
    if granted:
        cond = or_(cond, Shelf.id.in_(granted))
    return stmt.where(cond)


def _visible_work_condition(db: Session, user: User):
    """Build the SQL predicate selecting works the user may see (used by the query + id helpers).

    A work is visible when it is loose (in no shelf) OR it is on at least one see-able shelf
    (open/visible, or private with a grant). Implemented with EXISTS sub-queries over ALIASED
    ShelfWork/Shelf so the predicate can be combined with an outer query that itself joins
    ShelfWork (e.g. ``list_works?shelf_id=...``) without SQLAlchemy auto-correlation breaking the
    sub-query's FROM clause.
    """
    from sqlalchemy.orm import aliased

    granted = granted_target_ids(db, user, "shelf")
    sw = aliased(ShelfWork)
    sw2 = aliased(ShelfWork)
    sh = aliased(Shelf)

    # The work is on no shelf at all (loose -> open).
    loose = ~exists(select(sw.work_id).where(sw.work_id == Work.id))

    # The work is on at least one open/visible shelf.
    open_visible = exists(
        select(sw2.work_id)
        .join(sh, sh.id == sw2.shelf_id)
        .where(
            sw2.work_id == Work.id,
            sh.access_level.in_(_OPEN_OR_VISIBLE),
        )
    )

    conditions = [loose, open_visible]
    if granted:
        # The work is on a (private) shelf the user holds a grant for.
        sw3 = aliased(ShelfWork)
        granted_cond = exists(
            select(sw3.work_id).where(
                sw3.work_id == Work.id,
                sw3.shelf_id.in_(granted),
            )
        )
        conditions.append(granted_cond)
    return or_(*conditions)


def visible_works_query(db: Session, user: User) -> Select:
    """Return a ``select(Work)`` filtered to the works the user may see.

    Admin/owner get an unfiltered query.
    """
    stmt = select(Work)
    if is_admin_or_owner(user):
        return stmt
    return stmt.where(_visible_work_condition(db, user))


def can_see_file(db: Session, user: User, file_id: uuid.UUID) -> bool:
    """True if the user may see a file.

    A file is see-able when it is linked to no work (loose) OR to at least one work the user may
    see. Mirrors the paper most-permissive rule across the file's owning works.
    """
    from app.models.file import FileWorkLink

    if is_admin_or_owner(user):
        return True
    work_ids = list(
        db.scalars(select(FileWorkLink.work_id).where(FileWorkLink.file_id == file_id)).all()
    )
    if not work_ids:
        return True  # loose file -> open
    visible = visible_work_ids(db, user)
    if visible is None:
        return True
    return any(wid in visible for wid in work_ids)


def can_see_scope_container(
    db: Session, user: User, *, scope_type: str, scope_id: uuid.UUID | str | None
) -> bool:
    """True if the user may see the container behind a ``library``/``shelf``/``rack`` scope.

    ``library`` (and any scope without an id) is always allowed — the actual works are still
    filtered by ``visible_work_ids``. A ``shelf``/``rack`` scope additionally requires SEE on that
    specific container; a missing/unparsable container resolves to allowed (the work filter then
    yields nothing).
    """
    if is_admin_or_owner(user):
        return True
    if scope_id is None or scope_type not in ("shelf", "rack"):
        return True
    if isinstance(scope_id, str):
        try:
            scope_id = uuid.UUID(scope_id)
        except ValueError:
            return True
    if scope_type == "shelf":
        shelf = db.get(Shelf, scope_id)
        return shelf is None or can_see_shelf(db, user, shelf)
    rack = db.get(Rack, scope_id)
    return rack is None or can_see_rack(db, user, rack)


def visible_work_ids(db: Session, user: User) -> set[uuid.UUID] | None:
    """Return the set of work ids the user may see, or ``None`` if unrestricted (admin/owner).

    ``None`` is a short-circuit sentinel: callers should treat it as "no filtering needed".
    """
    if is_admin_or_owner(user):
        return None
    return set(db.scalars(select(Work.id).where(_visible_work_condition(db, user))).all())


__all__ = [
    "is_admin_or_owner",
    "user_group_ids",
    "granted_target_ids",
    "can_see_rack",
    "can_see_shelf",
    "can_modify_rack",
    "can_modify_shelf",
    "can_see_work",
    "can_modify_work",
    "can_see_scope_container",
    "can_see_file",
    "visible_racks_query",
    "visible_shelves_query",
    "visible_works_query",
    "visible_work_ids",
]
