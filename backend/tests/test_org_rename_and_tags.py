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
