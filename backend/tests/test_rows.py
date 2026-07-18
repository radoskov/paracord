"""Rows grouping layer — HTTP endpoint + access/scope/tag integration tests.

Row is the broadest grouping layer: Row ⊃ Rack ⊃ Shelf ⊃ Paper. A paper's row is inferred
work→shelf→rack→row. These exercise the /rows endpoints, the row scope, tag scoping to rows, and
access control (a private row is hidden from a reader, visible to the owner)."""

from app.models.organization import Rack, RackShelf, Row, RowRack, Shelf, ShelfWork
from app.models.work import Work


def _row_id(resp) -> str:
    return resp.json()["id"]


def test_row_crud_and_rack_membership(client, auth_headers, db) -> None:
    owner = auth_headers("owner")
    # Create a row + two racks.
    r = client.post("/api/v1/rows", headers=owner, json={"name": "Row One", "description": "d"})
    assert r.status_code == 201
    row_id = _row_id(r)
    rack1 = client.post("/api/v1/racks", headers=owner, json={"name": "Rack 1"}).json()["id"]
    rack2 = client.post("/api/v1/racks", headers=owner, json={"name": "Rack 2"}).json()["id"]

    # It appears in the list, carries its description.
    listed = client.get("/api/v1/rows", headers=owner).json()
    assert any(x["id"] == row_id and x["description"] == "d" for x in listed)

    # Add both racks; the membership endpoint lists them.
    assert (
        client.post(
            f"/api/v1/rows/{row_id}/racks", headers=owner, json={"rack_id": rack1}
        ).status_code
        == 204
    )
    assert (
        client.post(
            f"/api/v1/rows/{row_id}/racks", headers=owner, json={"rack_id": rack2}
        ).status_code
        == 204
    )
    racks = client.get(f"/api/v1/rows/{row_id}/racks", headers=owner).json()
    assert {x["id"] for x in racks} == {rack1, rack2}

    # Remove one rack.
    assert client.delete(f"/api/v1/rows/{row_id}/racks/{rack1}", headers=owner).status_code == 204
    racks = client.get(f"/api/v1/rows/{row_id}/racks", headers=owner).json()
    assert {x["id"] for x in racks} == {rack2}

    # Rename / edit.
    upd = client.patch(f"/api/v1/rows/{row_id}", headers=owner, json={"name": "Row Renamed"})
    assert upd.status_code == 200 and upd.json()["name"] == "Row Renamed"

    # Delete the row WITHOUT deleting racks — the rack survives.
    assert client.delete(f"/api/v1/rows/{row_id}", headers=owner).status_code == 204
    assert all(x["id"] != row_id for x in client.get("/api/v1/rows", headers=owner).json())
    assert any(x["id"] == rack2 for x in client.get("/api/v1/racks", headers=owner).json())


def test_delete_row_cascade_deletes_racks_when_requested(client, auth_headers, db) -> None:
    owner = auth_headers("owner")
    row_id = _row_id(client.post("/api/v1/rows", headers=owner, json={"name": "Cascade"}))
    rack = client.post("/api/v1/racks", headers=owner, json={"name": "Doomed Rack"}).json()["id"]
    client.post(f"/api/v1/rows/{row_id}/racks", headers=owner, json={"rack_id": rack})

    assert (
        client.delete(f"/api/v1/rows/{row_id}?delete_racks=true", headers=owner).status_code == 204
    )
    # The rack is gone too.
    assert all(x["id"] != rack for x in client.get("/api/v1/racks", headers=owner).json())


def test_row_scope_and_tag_scope_via_api(client, auth_headers, db) -> None:
    owner = auth_headers("owner")
    # Build work → shelf → rack → row directly, then verify the row scope resolves the paper.
    work = Work(canonical_title="Scoped", normalized_title="scoped")
    shelf = Shelf(name="S", access_level="open")
    rack = Rack(name="R", access_level="open")
    row = Row(name="RowScope", access_level="open")
    db.add_all([work, shelf, rack, row])
    db.flush()
    db.add(ShelfWork(shelf_id=shelf.id, work_id=work.id))
    db.add(RackShelf(rack_id=rack.id, shelf_id=shelf.id))
    db.add(RowRack(row_id=row.id, rack_id=rack.id))
    db.commit()

    # row_id filter on the works list returns the paper.
    items = client.get(f"/api/v1/works?row_id={row.id}", headers=owner).json()["items"]
    assert any(w["id"] == str(work.id) for w in items)

    # A tag scoped to the row is offered to that paper (assignable), and the scope round-trips.
    tag_id = client.post("/api/v1/tags", headers=owner, json={"name": "RowTag"}).json()["id"]
    scoped = client.put(
        f"/api/v1/tags/{tag_id}/scope", headers=owner, json={"row_ids": [str(row.id)]}
    ).json()
    assert scoped["row_ids"] == [str(row.id)]
    assignable = client.get(f"/api/v1/tags/assignable?work_id={work.id}", headers=owner).json()
    assert any(t["id"] == tag_id for t in assignable)


def test_private_row_hidden_from_reader_visible_to_owner(client, auth_headers, db) -> None:
    owner = auth_headers("owner")
    reader = auth_headers("reader")
    row_id = _row_id(
        client.post(
            "/api/v1/rows", headers=owner, json={"name": "Secret Row", "access_level": "private"}
        )
    )
    # Owner (admin bypass) sees it; a plain reader without a grant does not.
    assert any(x["id"] == row_id for x in client.get("/api/v1/rows", headers=owner).json())
    assert all(x["id"] != row_id for x in client.get("/api/v1/rows", headers=reader).json())
    # A reader can't fetch its racks either (404, not a leak).
    assert client.get(f"/api/v1/rows/{row_id}/racks", headers=reader).status_code == 404


def test_row_modify_requires_librarian(client, auth_headers, db) -> None:
    owner = auth_headers("owner")
    row_id = _row_id(client.post("/api/v1/rows", headers=owner, json={"name": "Guarded"}))
    # A reader may not create or modify rows (librarian floor).
    assert (
        client.post(
            "/api/v1/rows", headers=auth_headers("reader"), json={"name": "Nope"}
        ).status_code
        == 403
    )
    assert (
        client.patch(
            f"/api/v1/rows/{row_id}", headers=auth_headers("reader"), json={"name": "x"}
        ).status_code
        == 403
    )
