"""No free-floating papers: the ephemeral default shelf (#1).

A newly created paper lands on the default shelf; filing it onto any real shelf removes it from the
default; removing it from its last real shelf drops it back onto the default. Uses the full-schema
client/db fixtures.
"""

from app.models.organization import Shelf, ShelfWork
from app.services.default_shelf import DEFAULT_SHELF_NAME, get_default_shelf_id


def _shelf_ids_for(db, work_id) -> set:
    return {sw.shelf_id for sw in db.query(ShelfWork).filter(ShelfWork.work_id == work_id).all()}


def test_new_paper_lands_on_default_shelf(client, auth_headers, db):
    r = client.post(
        "/api/v1/works", headers=auth_headers("editor"), json={"canonical_title": "Loose paper"}
    )
    assert r.status_code == 201
    work_id = r.json()["id"]
    default_id = get_default_shelf_id(db)
    assert default_id is not None
    import uuid as _uuid

    assert default_id in _shelf_ids_for(db, _uuid.UUID(work_id))
    # the default shelf is a real, named shelf
    assert db.get(Shelf, default_id).name == DEFAULT_SHELF_NAME


def test_filing_onto_real_shelf_removes_from_default(client, auth_headers, db):
    import uuid as _uuid

    work_id = client.post(
        "/api/v1/works", headers=auth_headers("editor"), json={"canonical_title": "Filed paper"}
    ).json()["id"]
    shelf_id = client.post(
        "/api/v1/shelves", headers=auth_headers("librarian"), json={"name": "Real shelf"}
    ).json()["id"]

    add = client.post(
        f"/api/v1/shelves/{shelf_id}/works",
        headers=auth_headers("librarian"),
        json={"work_id": work_id},
    )
    assert add.status_code in (200, 201, 204)
    db.expire_all()
    shelves = _shelf_ids_for(db, _uuid.UUID(work_id))
    assert _uuid.UUID(shelf_id) in shelves
    assert get_default_shelf_id(db) not in shelves  # ephemeral: left the default shelf


def test_removing_last_real_shelf_falls_back_to_default(client, auth_headers, db):
    import uuid as _uuid

    work_id = client.post(
        "/api/v1/works", headers=auth_headers("editor"), json={"canonical_title": "Fallback paper"}
    ).json()["id"]
    shelf_id = client.post(
        "/api/v1/shelves", headers=auth_headers("librarian"), json={"name": "Temp shelf"}
    ).json()["id"]
    client.post(
        f"/api/v1/shelves/{shelf_id}/works",
        headers=auth_headers("librarian"),
        json={"work_id": work_id},
    )
    # Remove from the only real shelf → must fall back onto the default shelf, never free-floating.
    rm = client.delete(
        f"/api/v1/shelves/{shelf_id}/works/{work_id}", headers=auth_headers("librarian")
    )
    assert rm.status_code == 204
    db.expire_all()
    shelves = _shelf_ids_for(db, _uuid.UUID(work_id))
    assert shelves == {get_default_shelf_id(db)}


def test_multiple_loose_papers_share_one_default_shelf_in_one_txn(db):
    """Regression: placing several loose papers before a flush must not create duplicate default
    shelves / AccessSettings singletons (UNIQUE violation on access_settings.id)."""
    from app.models.work import Work
    from app.services.default_shelf import get_default_shelf_id, place_on_default_if_loose

    works = [Work(canonical_title=f"p{i}", normalized_title=f"p{i}") for i in range(3)]
    db.add_all(works)
    db.flush()
    for w in works:
        place_on_default_if_loose(db, w.id)
    db.commit()  # must not raise

    default_id = get_default_shelf_id(db)
    assert default_id is not None
    for w in works:
        assert default_id in _shelf_ids_for(db, w.id)
