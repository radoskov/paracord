"""Rename shelf/rack/tag, tag delete-cascades-its-links, and a paper's applied tags (SEE-safe).

Covers the user-facing rename/reassign gaps: the shelf/rack rename endpoints already existed (with
their audit events); these lock in the role gate and event, and add the new tag rename/delete
endpoints plus the ``GET /works/{id}/tags`` applied-tags view.
"""

import uuid

from app.models.audit import AuditEvent
from app.models.organization import Tag, TagLink
from sqlalchemy import select


def _event_types(db, entity_id: str) -> list[str]:
    rows = db.scalars(select(AuditEvent)).all()
    return [r.event_type for r in rows if str(r.entity_id) == entity_id]


def test_rename_shelf_emits_modified_event(client, auth_headers, db) -> None:
    owner = auth_headers("owner")
    shelf_id = client.post("/api/v1/shelves", headers=owner, json={"name": "Old shelf"}).json()[
        "id"
    ]
    res = client.patch(f"/api/v1/shelves/{shelf_id}", headers=owner, json={"name": "New shelf"})
    assert res.status_code == 200
    assert res.json()["name"] == "New shelf"
    assert "shelf.modified" in _event_types(db, shelf_id)


def test_rename_shelf_requires_librarian_floor(client, auth_headers) -> None:
    owner = auth_headers("owner")
    shelf_id = client.post("/api/v1/shelves", headers=owner, json={"name": "Gated shelf"}).json()[
        "id"
    ]
    editor = auth_headers("editor")
    res = client.patch(f"/api/v1/shelves/{shelf_id}", headers=editor, json={"name": "Nope"})
    assert res.status_code == 403


def test_rename_rack_emits_modified_event(client, auth_headers, db) -> None:
    owner = auth_headers("owner")
    rack_id = client.post("/api/v1/racks", headers=owner, json={"name": "Old rack"}).json()["id"]
    res = client.patch(f"/api/v1/racks/{rack_id}", headers=owner, json={"name": "New rack"})
    assert res.status_code == 200
    assert res.json()["name"] == "New rack"
    assert "rack.modified" in _event_types(db, rack_id)


def test_rename_rack_requires_librarian_floor(client, auth_headers) -> None:
    owner = auth_headers("owner")
    rack_id = client.post("/api/v1/racks", headers=owner, json={"name": "Gated rack"}).json()["id"]
    editor = auth_headers("editor")
    res = client.patch(f"/api/v1/racks/{rack_id}", headers=editor, json={"name": "Nope"})
    assert res.status_code == 403


def test_rename_tag_updates_name_and_normalized(client, auth_headers, db) -> None:
    contributor = auth_headers("contributor")
    tag_id = client.post("/api/v1/tags", headers=contributor, json={"name": "Original tag"}).json()[
        "id"
    ]
    res = client.patch(
        f"/api/v1/tags/{tag_id}",
        headers=contributor,
        json={"name": "Renamed tag", "description": "now described", "color": "#123456"},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["name"] == "Renamed tag"
    assert body["normalized_name"] == "renamed tag"
    assert body["description"] == "now described"
    assert body["color"] == "#123456"
    assert "tag.modified" in _event_types(db, tag_id)


def test_rename_tag_requires_contributor_floor(client, auth_headers) -> None:
    owner = auth_headers("owner")
    tag_id = client.post("/api/v1/tags", headers=owner, json={"name": "Reader-gated"}).json()["id"]
    reader = auth_headers("reader")
    res = client.patch(f"/api/v1/tags/{tag_id}", headers=reader, json={"name": "Nope"})
    assert res.status_code == 403


def test_rename_tag_conflict_is_rejected(client, auth_headers) -> None:
    owner = auth_headers("owner")
    client.post("/api/v1/tags", headers=owner, json={"name": "Existing"})
    other_id = client.post("/api/v1/tags", headers=owner, json={"name": "Other"}).json()["id"]
    # Renaming "Other" onto the normalized name of "Existing" must not silently merge the tags.
    res = client.patch(f"/api/v1/tags/{other_id}", headers=owner, json={"name": "existing"})
    assert res.status_code == 409


def test_delete_tag_removes_its_links_but_keeps_the_paper(client, auth_headers, db) -> None:
    owner = auth_headers("owner")
    tag_id = client.post("/api/v1/tags", headers=owner, json={"name": "Doomed"}).json()["id"]
    work_id = client.post(
        "/api/v1/works", headers=owner, json={"canonical_title": "Tagged paper"}
    ).json()["id"]
    link = client.post(
        f"/api/v1/tags/{tag_id}/links",
        headers=owner,
        json={"entity_type": "work", "entity_id": work_id},
    )
    assert link.status_code == 204
    assert db.scalar(select(TagLink).where(TagLink.tag_id == uuid.UUID(tag_id))) is not None

    res = client.delete(f"/api/v1/tags/{tag_id}", headers=owner)
    assert res.status_code == 204
    # The tag and its link are gone; the paper survives and simply lost the tag.
    assert db.get(Tag, uuid.UUID(tag_id)) is None
    assert db.scalar(select(TagLink).where(TagLink.tag_id == uuid.UUID(tag_id))) is None
    assert client.get(f"/api/v1/works/{work_id}", headers=owner).status_code == 200
    assert "tag.deleted" in _event_types(db, tag_id)


def test_delete_tag_requires_editor_floor(client, auth_headers) -> None:
    owner = auth_headers("owner")
    tag_id = client.post("/api/v1/tags", headers=owner, json={"name": "Editor-gated"}).json()["id"]
    contributor = auth_headers("contributor")
    res = client.delete(f"/api/v1/tags/{tag_id}", headers=contributor)
    assert res.status_code == 403


def test_work_tags_lists_applied_tags(client, auth_headers) -> None:
    owner = auth_headers("owner")
    tag_id = client.post(
        "/api/v1/tags", headers=owner, json={"name": "Applied", "color": "#abcdef"}
    ).json()["id"]
    work_id = client.post(
        "/api/v1/works", headers=owner, json={"canonical_title": "Has a tag"}
    ).json()["id"]
    assert client.get(f"/api/v1/works/{work_id}/tags", headers=owner).json() == []
    client.post(
        f"/api/v1/tags/{tag_id}/links",
        headers=owner,
        json={"entity_type": "work", "entity_id": work_id},
    )
    res = client.get(f"/api/v1/works/{work_id}/tags", headers=owner)
    assert res.status_code == 200
    body = res.json()
    assert len(body) == 1
    assert body[0]["name"] == "Applied"
    assert body[0]["color"] == "#abcdef"


def test_work_tags_hidden_for_missing_work(client, auth_headers) -> None:
    owner = auth_headers("owner")
    res = client.get(f"/api/v1/works/{uuid.uuid4()}/tags", headers=owner)
    assert res.status_code == 404


def test_tag_scope_and_assignable_tags(client, auth_headers, db) -> None:
    """2026-07-16: a tag scoped to a shelf/rack is only OFFERED for papers there; global tags
    (no scope rows) are always offered."""
    owner = auth_headers("owner")
    contributor = auth_headers("contributor")

    shelf_id = client.post("/api/v1/shelves", headers=owner, json={"name": "Scoped shelf"}).json()["id"]
    other_shelf = client.post("/api/v1/shelves", headers=owner, json={"name": "Other shelf"}).json()["id"]
    rack_id = client.post("/api/v1/racks", headers=owner, json={"name": "Rack"}).json()["id"]
    client.post(f"/api/v1/racks/{rack_id}/shelves", headers=owner, json={"shelf_id": shelf_id})

    work_id = client.post(
        "/api/v1/works", headers=owner, json={"canonical_title": "Paper", "authors": []}
    ).json()["id"]
    client.post(f"/api/v1/shelves/{shelf_id}/works", headers=owner, json={"work_id": work_id})

    global_tag = client.post("/api/v1/tags", headers=contributor, json={"name": "global"}).json()["id"]
    shelf_tag = client.post("/api/v1/tags", headers=contributor, json={"name": "shelfscoped"}).json()["id"]
    rack_tag = client.post("/api/v1/tags", headers=contributor, json={"name": "rackscoped"}).json()["id"]
    off_tag = client.post("/api/v1/tags", headers=contributor, json={"name": "offscope"}).json()["id"]

    client.put(f"/api/v1/tags/{shelf_tag}/scope", headers=contributor, json={"shelf_ids": [shelf_id]})
    # rack-scoped: the paper qualifies via its shelf being in the rack.
    client.put(f"/api/v1/tags/{rack_tag}/scope", headers=contributor, json={"rack_ids": [rack_id]})
    # scoped to a shelf the paper is NOT on → must be excluded.
    client.put(f"/api/v1/tags/{off_tag}/scope", headers=contributor, json={"shelf_ids": [other_shelf]})

    res = client.get(f"/api/v1/tags/assignable?work_id={work_id}", headers=contributor)
    assert res.status_code == 200
    ids = {t["id"] for t in res.json()}
    assert global_tag in ids  # global always offered
    assert shelf_tag in ids  # scoped to the paper's shelf
    assert rack_tag in ids  # scoped to a rack containing the paper's shelf
    assert off_tag not in ids  # scoped elsewhere → not offered

    # The scope round-trips on the tag list.
    listed = {t["id"]: t for t in client.get("/api/v1/tags", headers=contributor).json()}
    assert listed[shelf_tag]["shelf_ids"] == [shelf_id]
    assert listed[rack_tag]["rack_ids"] == [rack_id]
    # Filtering the tag list by shelf returns the shelf tag + globals, not the off-scope one.
    by_shelf = {t["id"] for t in client.get(f"/api/v1/tags?shelf_id={shelf_id}", headers=contributor).json()}
    assert shelf_tag in by_shelf and global_tag in by_shelf and off_tag not in by_shelf
