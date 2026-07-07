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


# --- shelf hard-delete: orphaned papers fall back to the default shelf ---


def _new_paper(client, headers, title="P") -> str:
    return client.post("/api/v1/works", headers=headers, json={"canonical_title": title}).json()[
        "id"
    ]


def _new_shelf(client, headers, name) -> str:
    return client.post("/api/v1/shelves", headers=headers, json={"name": name}).json()["id"]


def test_delete_shelf_moves_only_there_papers_to_default(client, auth_headers, db):
    import uuid as _uuid

    owner = auth_headers("owner")
    work_id = _new_paper(client, owner, "only-here")
    shelf_id = _new_shelf(client, owner, "Solo shelf")
    client.post(f"/api/v1/shelves/{shelf_id}/works", headers=owner, json={"work_id": work_id})

    r = client.delete(f"/api/v1/shelves/{shelf_id}", headers=owner)
    assert r.status_code == 204
    assert client.get(f"/api/v1/shelves/{shelf_id}/works", headers=owner).status_code == 404
    db.expire_all()
    # Was only on the deleted shelf → now back on the default shelf (never free-floating).
    assert _shelf_ids_for(db, _uuid.UUID(work_id)) == {get_default_shelf_id(db)}


def test_delete_shelf_leaves_papers_on_other_shelves(client, auth_headers, db):
    import uuid as _uuid

    owner = auth_headers("owner")
    work_id = _new_paper(client, owner, "multi-home")
    shelf_a = _new_shelf(client, owner, "Shelf A")
    shelf_b = _new_shelf(client, owner, "Shelf B")
    client.post(f"/api/v1/shelves/{shelf_a}/works", headers=owner, json={"work_id": work_id})
    client.post(f"/api/v1/shelves/{shelf_b}/works", headers=owner, json={"work_id": work_id})

    assert client.delete(f"/api/v1/shelves/{shelf_a}", headers=owner).status_code == 204
    db.expire_all()
    shelves = _shelf_ids_for(db, _uuid.UUID(work_id))
    # Still on B; not re-added to default (it wasn't orphaned); A's association is gone.
    assert shelves == {_uuid.UUID(shelf_b)}


def test_cannot_delete_default_shelf(client, auth_headers, db):
    owner = auth_headers("owner")
    _new_paper(client, owner, "bootstrap default")  # ensures the default shelf exists
    default_id = get_default_shelf_id(db)
    assert default_id is not None
    r = client.delete(f"/api/v1/shelves/{default_id}", headers=owner)
    assert r.status_code == 400


def test_delete_shelf_requires_modify_permission(client, auth_headers, db):
    owner = auth_headers("owner")
    shelf_id = _new_shelf(client, owner, "Perm shelf")
    # A reader is below the librarian floor for structural changes.
    assert (
        client.delete(f"/api/v1/shelves/{shelf_id}", headers=auth_headers("reader")).status_code
        == 403
    )


# --- rack hard-delete: optional cascade to shelves ---


def _new_rack(client, headers, name) -> str:
    return client.post("/api/v1/racks", headers=headers, json={"name": name}).json()["id"]


def _add_shelf_to_rack(client, headers, rack_id, shelf_id):
    return client.post(
        f"/api/v1/racks/{rack_id}/shelves", headers=headers, json={"shelf_id": shelf_id}
    )


def test_delete_rack_keeps_shelves_by_default(client, auth_headers, db):
    owner = auth_headers("owner")
    shelf_id = _new_shelf(client, owner, "Kept shelf")
    rack_id = _new_rack(client, owner, "Rack keep")
    _add_shelf_to_rack(client, owner, rack_id, shelf_id)

    r = client.delete(f"/api/v1/racks/{rack_id}", headers=owner)  # delete_shelves defaults false
    assert r.status_code == 204
    assert client.get(f"/api/v1/racks/{rack_id}/shelves", headers=owner).status_code == 404
    # The shelf survived (just left the rack).
    assert client.get(f"/api/v1/shelves/{shelf_id}/works", headers=owner).status_code == 200


def test_delete_rack_with_shelves_cascades_and_falls_back_to_default(client, auth_headers, db):
    import uuid as _uuid

    owner = auth_headers("owner")
    only_paper = _new_paper(client, owner, "rack-only paper")
    shared_paper = _new_paper(client, owner, "rack-shared paper")
    in_rack = _new_shelf(client, owner, "In-rack shelf")
    other = _new_shelf(client, owner, "Other shelf")
    for wid in (only_paper, shared_paper):
        client.post(f"/api/v1/shelves/{in_rack}/works", headers=owner, json={"work_id": wid})
    client.post(f"/api/v1/shelves/{other}/works", headers=owner, json={"work_id": shared_paper})
    rack_id = _new_rack(client, owner, "Rack cascade")
    _add_shelf_to_rack(client, owner, rack_id, in_rack)

    r = client.delete(f"/api/v1/racks/{rack_id}?delete_shelves=true", headers=owner)
    assert r.status_code == 204
    db.expire_all()
    # The in-rack shelf was hard-deleted.
    assert client.get(f"/api/v1/shelves/{in_rack}/works", headers=owner).status_code == 404
    # Paper only on the deleted shelf → default; paper also on 'other' stays there (not default).
    assert _shelf_ids_for(db, _uuid.UUID(only_paper)) == {get_default_shelf_id(db)}
    assert _shelf_ids_for(db, _uuid.UUID(shared_paper)) == {_uuid.UUID(other)}


def test_delete_rack_requires_modify_permission(client, auth_headers):
    owner = auth_headers("owner")
    rack_id = _new_rack(client, owner, "Perm rack")
    assert (
        client.delete(f"/api/v1/racks/{rack_id}", headers=auth_headers("reader")).status_code == 403
    )


# --- D11: idempotent startup backfill of loose papers ---


def test_backfill_places_loose_papers_and_is_idempotent(db):
    """A paper created on no shelf is placed onto the default shelf by the startup backfill (D11)."""
    import uuid as _uuid

    from app.models.organization import ShelfWork
    from app.models.work import Work
    from app.services.default_shelf import backfill_loose_papers_onto_default, get_default_shelf_id

    # A directly-created work with no shelf membership (simulates a pre-invariant / mid-deploy row).
    work = Work(canonical_title="Loose", normalized_title="loose")
    db.add(work)
    db.commit()
    assert (
        db.query(ShelfWork).filter(ShelfWork.work_id == work.id).count() == 0
    )  # loose to begin with

    placed = backfill_loose_papers_onto_default(db)
    db.commit()
    assert placed == 1
    default_id = get_default_shelf_id(db)
    assert {sw.shelf_id for sw in db.query(ShelfWork).filter(ShelfWork.work_id == work.id)} == {
        default_id
    }

    # Idempotent: a second run places nothing (the paper is no longer loose).
    assert backfill_loose_papers_onto_default(db) == 0
    assert isinstance(default_id, _uuid.UUID)


def test_backfill_no_op_when_nothing_loose(db):
    from app.services.default_shelf import backfill_loose_papers_onto_default

    assert backfill_loose_papers_onto_default(db) == 0


# --- L1: the shelf-list read flags the default shelf so the UI can exclude it as a move-target ---


def test_shelf_list_flags_default_shelf(client, auth_headers, db):
    """GET /shelves marks the default/Inbox shelf with is_default=True and real shelves False, so
    the frontend can drop the default shelf from "Put into" menus without hardcoding a name."""
    owner = auth_headers("owner")
    _new_paper(client, owner, "bootstrap default")  # ensures the default shelf exists
    real_id = _new_shelf(client, owner, "A real shelf")
    default_id = str(get_default_shelf_id(db))

    shelves = client.get("/api/v1/shelves", headers=owner).json()
    by_id = {s["id"]: s for s in shelves}
    assert by_id[default_id]["is_default"] is True
    assert by_id[real_id]["is_default"] is False
