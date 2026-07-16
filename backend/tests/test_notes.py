"""Notes: per-paper notes field + per-Insights-scope notes (2026-07-16)."""


def test_work_notes_persist_and_read_back(client, auth_headers, db) -> None:
    owner = auth_headers("owner")
    work_id = client.post(
        "/api/v1/works", headers=owner, json={"canonical_title": "Paper", "authors": []}
    ).json()["id"]

    r = client.patch(f"/api/v1/works/{work_id}", headers=owner, json={"notes": "My private note."})
    assert r.status_code == 200
    assert r.json()["notes"] == "My private note."

    got = client.get(f"/api/v1/works/{work_id}", headers=owner)
    assert got.json()["notes"] == "My private note."

    # Notes can be updated and cleared.
    client.patch(f"/api/v1/works/{work_id}", headers=owner, json={"notes": "Updated."})
    assert client.get(f"/api/v1/works/{work_id}", headers=owner).json()["notes"] == "Updated."


def test_scope_notes_upsert_read_and_list(client, auth_headers, db) -> None:
    owner = auth_headers("owner")
    shelf_id = client.post("/api/v1/shelves", headers=owner, json={"name": "My shelf"}).json()["id"]

    # Empty before writing.
    empty = client.get("/api/v1/ai/scope-notes/latest?scope_type=library", headers=owner)
    assert empty.status_code == 200 and empty.json()["text"] == ""

    # Upsert a library-scope note and a shelf-scope note.
    client.put(
        "/api/v1/ai/scope-notes",
        headers=owner,
        json={"scope_type": "library", "text": "Whole-library note."},
    )
    r = client.put(
        "/api/v1/ai/scope-notes",
        headers=owner,
        json={"scope_type": "shelf", "scope_id": shelf_id, "text": "Shelf note."},
    )
    assert r.status_code == 200
    assert r.json()["text"] == "Shelf note."
    assert r.json()["scope_label"] == "My shelf"  # scope label resolved for the header

    # Read back the shelf note specifically.
    got = client.get(
        f"/api/v1/ai/scope-notes/latest?scope_type=shelf&scope_id={shelf_id}", headers=owner
    )
    assert got.json()["text"] == "Shelf note."

    # The list contains both (non-empty) notes with their scope labels.
    listed = client.get("/api/v1/ai/scope-notes", headers=owner).json()
    texts = {n["text"] for n in listed}
    assert "Whole-library note." in texts and "Shelf note." in texts

    # Clearing a note (empty text) drops it from the list.
    client.put(
        "/api/v1/ai/scope-notes",
        headers=owner,
        json={"scope_type": "shelf", "scope_id": shelf_id, "text": ""},
    )
    texts_after = {n["text"] for n in client.get("/api/v1/ai/scope-notes", headers=owner).json()}
    assert "Shelf note." not in texts_after
