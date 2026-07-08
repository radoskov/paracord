"""Additional access-policy contract tests.

These tests exercise stable product rules rather than endpoint implementation details:

* loose papers are visible and editor-modifiable;
* contributors may modify only their own loose/open papers;
* a paper on multiple shelves uses the most permissive governing shelf;
* visible/private shelves need group grants for modification;
* merged shadows are never returned by visibility filters.
"""

from __future__ import annotations

from app.models.group import Group, GroupGrant, GroupMembership
from app.models.organization import Shelf, ShelfWork
from app.models.work import Work
from app.services.access import (
    can_modify_shelf,
    can_modify_work,
    can_see_shelf,
    can_see_work,
    visible_work_ids,
)


def _work(db, title: str, *, created_by_user_id=None, merged_into_id=None) -> Work:
    work = Work(
        canonical_title=title,
        normalized_title=title.lower(),
        created_by_user_id=created_by_user_id,
        merged_into_id=merged_into_id,
    )
    db.add(work)
    db.flush()
    return work


def _shelf(db, name: str, *, access_level: str) -> Shelf:
    shelf = Shelf(name=name, access_level=access_level)
    db.add(shelf)
    db.flush()
    return shelf


def _put_on_shelf(db, work: Work, shelf: Shelf) -> None:
    db.add(ShelfWork(work_id=work.id, shelf_id=shelf.id))
    db.flush()


def _grant_shelf_to_user(db, user, shelf: Shelf) -> None:
    group = Group(name=f"grant-{user.username}-{shelf.name}")
    db.add(group)
    db.flush()
    db.add(GroupMembership(group_id=group.id, user_id=user.id))
    db.add(GroupGrant(group_id=group.id, target_type="shelf", target_id=shelf.id))
    db.flush()


def test_loose_work_visibility_and_contributor_own_only_modify(db, make_user) -> None:
    reader = make_user("access-reader", role="reader")
    contributor = make_user("access-contributor", role="contributor")
    editor = make_user("access-editor", role="editor")

    own = _work(db, "Contributor owned loose paper", created_by_user_id=contributor.id)
    other = _work(db, "Other loose paper")
    db.commit()

    assert can_see_work(db, reader, own)
    assert can_see_work(db, reader, other)

    assert can_modify_work(db, contributor, own)
    assert not can_modify_work(db, contributor, other)
    assert can_modify_work(db, editor, other)


def test_most_permissive_shelf_governs_visibility_and_editing(db, make_user) -> None:
    reader = make_user("multi-reader", role="reader")
    editor = make_user("multi-editor", role="editor")

    private = _shelf(db, "Private shelf", access_level="private")
    open_shelf = _shelf(db, "Open shelf", access_level="open")
    work = _work(db, "Paper in both private and open shelves")
    _put_on_shelf(db, work, private)
    _put_on_shelf(db, work, open_shelf)
    db.commit()

    assert can_see_work(db, reader, work)
    assert can_modify_work(db, editor, work)


def test_private_only_work_needs_grant_but_open_shelf_overrides(db, make_user) -> None:
    reader = make_user("private-reader", role="reader")
    editor = make_user("private-editor", role="editor")
    librarian = make_user("private-librarian", role="librarian")

    private = _shelf(db, "Private only", access_level="private")
    work = _work(db, "Private-only paper")
    _put_on_shelf(db, work, private)
    db.commit()

    assert not can_see_shelf(db, reader, private)
    assert not can_see_work(db, reader, work)
    assert not can_modify_work(db, editor, work)
    assert not can_modify_shelf(db, librarian, private)

    _grant_shelf_to_user(db, librarian, private)
    db.commit()

    assert can_see_shelf(db, librarian, private)
    assert can_modify_shelf(db, librarian, private)
    assert can_modify_work(db, librarian, work)


def test_visible_shelf_is_public_read_but_grant_gated_modify(db, make_user) -> None:
    reader = make_user("visible-reader", role="reader")
    librarian = make_user("visible-librarian", role="librarian")
    visible = _shelf(db, "Visible shelf", access_level="visible")
    work = _work(db, "Visible shelf paper")
    _put_on_shelf(db, work, visible)
    db.commit()

    assert can_see_shelf(db, reader, visible)
    assert can_see_work(db, reader, work)
    assert not can_modify_shelf(db, librarian, visible)

    _grant_shelf_to_user(db, librarian, visible)
    db.commit()

    assert can_modify_shelf(db, librarian, visible)
    assert can_modify_work(db, librarian, work)


def test_visible_work_ids_excludes_merged_shadows(db, make_user) -> None:
    reader = make_user("shadow-reader", role="reader")
    base = _work(db, "Visible base work")
    shadow = _work(db, "Merged shadow work", merged_into_id=base.id)
    db.commit()

    visible = visible_work_ids(db, reader)

    assert visible is not None
    assert base.id in visible
    assert shadow.id not in visible
