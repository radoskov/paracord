"""Saved-filter tests (Phase B7): CRUD, per-user isolation, and the visibility clamp.

Uses the HTTP conftest fixtures (real FastAPI app on in-memory SQLite with the full model
metadata, so the ``saved_filters`` table exists). Covers create/list/update/delete + duplicate
409, per-user isolation (B can't see or mutate A's -> 404 not 403), and the critical resolution +
visibility clamp: a saved filter run by a low-visibility user excludes hidden works while owner/
admin are unrestricted. Also exercises the graph + export scopes over a saved filter.
"""

import uuid

from app.models.organization import Shelf, ShelfWork
from app.models.saved_filter import SavedFilter
from app.models.work import Work
from app.services.auth import create_user_session
from app.services.saved_filters import resolve_saved_filter_work_ids


def _auth(db, make_user, role: str = "reader", username: str | None = None):
    """Create a user of ``role`` and return (user, bearer-headers)."""
    user = make_user(username or f"{role}-{uuid.uuid4().hex[:8]}", role=role)
    token, _session = create_user_session(db, user, ttl_minutes=60)
    db.commit()
    return user, {"Authorization": f"Bearer {token}"}


def _work(db, *, title: str, reading_status: str = "unread") -> Work:
    work = Work(
        canonical_title=title, normalized_title=title.lower(), reading_status=reading_status
    )
    db.add(work)
    db.commit()
    db.refresh(work)
    return work


# --------------------------------------------------------------------------------------------------
# CRUD + duplicate 409
# --------------------------------------------------------------------------------------------------
def test_crud_lifecycle_and_duplicate_conflict(client, db, make_user):
    _user, headers = _auth(db, make_user, role="reader")

    # Create
    created = client.post(
        "/api/v1/saved-filters",
        headers=headers,
        json={
            "name": "Recent unread",
            "search_mode": "metadata",
            "query_text": "transformer",
            "params": {"reading_status": "unread", "missing": ["doi"]},
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    filter_id = body["id"]
    assert body["name"] == "Recent unread"
    assert body["params"]["reading_status"] == "unread"
    assert body["params"]["missing"] == ["doi"]

    # List
    listed = client.get("/api/v1/saved-filters", headers=headers)
    assert listed.status_code == 200
    assert [f["id"] for f in listed.json()] == [filter_id]

    # Duplicate name -> 409
    dup = client.post("/api/v1/saved-filters", headers=headers, json={"name": "Recent unread"})
    assert dup.status_code == 409

    # Update
    updated = client.put(
        f"/api/v1/saved-filters/{filter_id}",
        headers=headers,
        json={"name": "Renamed", "query_text": "attention"},
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Renamed"
    assert updated.json()["query_text"] == "attention"

    # Delete
    deleted = client.delete(f"/api/v1/saved-filters/{filter_id}", headers=headers)
    assert deleted.status_code == 204
    assert client.get("/api/v1/saved-filters", headers=headers).json() == []


# --------------------------------------------------------------------------------------------------
# Per-user isolation
# --------------------------------------------------------------------------------------------------
def test_per_user_isolation(client, db, make_user):
    _a, headers_a = _auth(db, make_user, role="reader", username="alice")
    _b, headers_b = _auth(db, make_user, role="reader", username="bob")

    a_id = client.post(
        "/api/v1/saved-filters", headers=headers_a, json={"name": "Alice filter"}
    ).json()["id"]

    # B's list excludes A's.
    assert client.get("/api/v1/saved-filters", headers=headers_b).json() == []

    # B cannot update or delete A's -> 404 (not 403; hides existence).
    assert (
        client.put(
            f"/api/v1/saved-filters/{a_id}", headers=headers_b, json={"name": "hijack"}
        ).status_code
        == 404
    )
    assert client.delete(f"/api/v1/saved-filters/{a_id}", headers=headers_b).status_code == 404

    # A's filter is untouched.
    assert (
        client.get("/api/v1/saved-filters", headers=headers_a).json()[0]["name"] == "Alice filter"
    )

    # Same name is allowed for a different owner (uniqueness is per-owner).
    assert (
        client.post(
            "/api/v1/saved-filters", headers=headers_b, json={"name": "Alice filter"}
        ).status_code
        == 201
    )


# --------------------------------------------------------------------------------------------------
# Resolution + visibility clamp (critical)
# --------------------------------------------------------------------------------------------------
def _seed_visible_and_hidden(db):
    """Two 'unread' works: one loose (open -> visible to all), one on a private shelf (hidden)."""
    visible = _work(db, title="Visible unread", reading_status="unread")
    hidden = _work(db, title="Hidden unread", reading_status="unread")
    private = Shelf(name="private-shelf", access_level="private")
    db.add(private)
    db.flush()
    db.add(ShelfWork(shelf_id=private.id, work_id=hidden.id))
    db.commit()
    return visible, hidden


def test_resolution_visibility_clamp(db, make_user):
    visible, hidden = _seed_visible_and_hidden(db)
    reader = make_user("clamp-reader", role="reader")
    owner = make_user("clamp-owner", role="owner")

    # An 'unread' saved filter owned by the low-visibility reader.
    saved = SavedFilter(
        owner_user_id=reader.id,
        name="unread",
        search_mode="metadata",
        query_text=None,
        params={"reading_status": "unread", "missing": []},
    )
    db.add(saved)
    db.commit()
    db.refresh(saved)

    reader_ids = set(resolve_saved_filter_work_ids(db, reader, saved))
    assert visible.id in reader_ids
    assert hidden.id not in reader_ids  # clamped: the private-shelf work is excluded

    # An owner (unrestricted) sees both.
    owner_ids = set(resolve_saved_filter_work_ids(db, owner, saved))
    assert {visible.id, hidden.id} <= owner_ids


# --------------------------------------------------------------------------------------------------
# Graph + export scopes over a saved filter (both return the clamped set)
# --------------------------------------------------------------------------------------------------
def test_graph_scope_saved_filter_clamped(client, db, make_user):
    visible, hidden = _seed_visible_and_hidden(db)
    reader, headers = _auth(db, make_user, role="reader", username="graph-reader")
    saved = SavedFilter(
        owner_user_id=reader.id, name="unread", params={"reading_status": "unread", "missing": []}
    )
    db.add(saved)
    db.commit()
    db.refresh(saved)

    resp = client.post(
        "/api/v1/graphs/citation",
        headers=headers,
        json={"scope": {"type": "saved_filter", "id": str(saved.id)}, "node_mode": "local_only"},
    )
    assert resp.status_code == 200, resp.text
    work_ids = {n["work_id"] for n in resp.json()["nodes"] if n["work_id"]}
    assert str(visible.id) in work_ids
    assert str(hidden.id) not in work_ids

    # A saved filter owned by someone else -> 404 scope.
    other, other_headers = _auth(db, make_user, role="reader", username="graph-other")
    assert (
        client.post(
            "/api/v1/graphs/citation",
            headers=other_headers,
            json={"scope": {"type": "saved_filter", "id": str(saved.id)}},
        ).status_code
        == 404
    )


def test_export_scope_saved_filter_clamped(client, db, make_user):
    visible, hidden = _seed_visible_and_hidden(db)
    reader, headers = _auth(db, make_user, role="reader", username="export-reader")
    saved = SavedFilter(
        owner_user_id=reader.id, name="unread", params={"reading_status": "unread", "missing": []}
    )
    db.add(saved)
    db.commit()
    db.refresh(saved)

    resp = client.post(
        "/api/v1/exports",
        headers=headers,
        json={"scope_type": "saved_filter", "scope_id": str(saved.id), "format": "text"},
    )
    assert resp.status_code == 200, resp.text
    content = resp.json()["content"]
    assert "Visible unread" in content
    assert "Hidden unread" not in content  # clamped out of the export

    # Foreign filter -> 404.
    _other, other_headers = _auth(db, make_user, role="reader", username="export-other")
    assert (
        client.post(
            "/api/v1/exports",
            headers=other_headers,
            json={"scope_type": "saved_filter", "scope_id": str(saved.id), "format": "text"},
        ).status_code
        == 404
    )
