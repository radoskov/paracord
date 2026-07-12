"""Shared scope resolver (S1/S2): query composition, shadow filter, required visibility clamp."""

import uuid

import pytest
from app.models.organization import Rack, RackShelf, Shelf, ShelfWork
from app.models.work import Work
from app.services.scope_resolution import (
    count_scope_works,
    resolve_scope_works,
    scope_works_query,
)


def _work(db, title, **kw) -> Work:
    work = Work(canonical_title=title, normalized_title=title.lower(), **kw)
    db.add(work)
    db.flush()
    return work


def _shelf_with(db, *works) -> Shelf:
    shelf = Shelf(name="S")
    db.add(shelf)
    db.flush()
    for w in works:
        db.add(ShelfWork(shelf_id=shelf.id, work_id=w.id))
    db.flush()
    return shelf


def test_library_scope_excludes_merged_shadows(db) -> None:
    keep = _work(db, "Keep")
    shadow = _work(db, "Shadow")
    shadow.merged_into_id = keep.id
    db.flush()
    works = resolve_scope_works(db, "library", None, visible_ids=None)
    assert [w.id for w in works] == [keep.id]


def test_shelf_and_rack_scopes(db) -> None:
    a, b, c = _work(db, "A"), _work(db, "B"), _work(db, "C")
    shelf = _shelf_with(db, a, b)
    rack = Rack(name="R")
    db.add(rack)
    db.flush()
    db.add(RackShelf(rack_id=rack.id, shelf_id=shelf.id))
    db.flush()
    assert {w.id for w in resolve_scope_works(db, "shelf", shelf.id, visible_ids=None)} == {
        a.id,
        b.id,
    }
    assert {w.id for w in resolve_scope_works(db, "rack", rack.id, visible_ids=None)} == {
        a.id,
        b.id,
    }
    assert c.id not in {w.id for w in resolve_scope_works(db, "rack", rack.id, visible_ids=None)}


def test_visible_ids_clamp_is_applied_in_sql(db) -> None:
    a = _work(db, "A")
    _work(db, "Hidden")
    works = resolve_scope_works(db, "library", None, visible_ids={a.id})
    assert [w.id for w in works] == [a.id]
    # An empty SEE-set means no rows — not "everything".
    assert resolve_scope_works(db, "library", None, visible_ids=set()) == []


def test_visible_ids_is_a_required_parameter() -> None:
    """S2: forgetting the clamp is a TypeError at the call site, not a silent leak."""
    with pytest.raises(TypeError):
        scope_works_query("library", None)  # type: ignore[call-arg]


def test_count_matches_resolution_without_loading(db) -> None:
    a, b = _work(db, "A"), _work(db, "B")
    shelf = _shelf_with(db, a, b)
    assert count_scope_works(db, "shelf", shelf.id, visible_ids=None) == 2
    assert count_scope_works(db, "library", None, visible_ids={a.id}) == 1


def test_bad_scope_raises_value_error(db) -> None:
    with pytest.raises(ValueError, match="scope id is required"):
        resolve_scope_works(db, "shelf", None, visible_ids=None)
    with pytest.raises(ValueError, match="Unsupported scope type"):
        resolve_scope_works(db, "galaxy", uuid.uuid4(), visible_ids=None)
