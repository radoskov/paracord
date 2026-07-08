"""Slow supplementary visibility-scope invariants.

These tests create a moderately larger in-memory corpus to catch query/filter drift
without requiring timing-based performance assertions.
"""

from __future__ import annotations

import pytest
from app.models.organization import Shelf, ShelfWork
from app.models.work import Work
from app.services.access import visible_work_ids

pytestmark = pytest.mark.slow


def test_visibility_filter_handles_many_open_private_and_shadowed_works(db, make_user) -> None:
    reader = make_user("slow-visibility-reader", role="reader")
    open_shelf = Shelf(name="bulk open", access_level="open")
    private_shelf = Shelf(name="bulk private", access_level="private")
    db.add_all([open_shelf, private_shelf])
    db.flush()

    open_works: list[Work] = []
    private_works: list[Work] = []
    shadow_works: list[Work] = []

    for i in range(80):
        work = Work(canonical_title=f"Open bulk {i}", normalized_title=f"open bulk {i}")
        db.add(work)
        db.flush()
        db.add(ShelfWork(shelf_id=open_shelf.id, work_id=work.id))
        open_works.append(work)

    for i in range(40):
        work = Work(canonical_title=f"Private bulk {i}", normalized_title=f"private bulk {i}")
        db.add(work)
        db.flush()
        db.add(ShelfWork(shelf_id=private_shelf.id, work_id=work.id))
        private_works.append(work)

    for i, base in enumerate(open_works[:10]):
        shadow = Work(
            canonical_title=f"Shadow bulk {i}",
            normalized_title=f"shadow bulk {i}",
            merged_into_id=base.id,
        )
        db.add(shadow)
        db.flush()
        db.add(ShelfWork(shelf_id=open_shelf.id, work_id=shadow.id))
        shadow_works.append(shadow)

    db.commit()

    visible = visible_work_ids(db, reader)

    assert visible is not None
    assert {work.id for work in open_works} <= visible
    assert not ({work.id for work in private_works} & visible)
    assert not ({work.id for work in shadow_works} & visible)
