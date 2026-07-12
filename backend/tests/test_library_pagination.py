"""D18 server-controlled Library pagination + D32 shelves/racks columns.

Covers the paginated ``GET /api/v1/works`` envelope (total/pages/page clamp, per_page override,
per-user preference, admin global-max clamp), the profile ``papers_per_page`` round-trip, the admin
global-max round-trip, and that each row carries its SEE-filtered shelves/racks (a reader never sees
the name of a shelf/rack they cannot access).
"""

import uuid

from app.models.organization import Rack, RackShelf, Shelf, ShelfWork
from app.models.user import User
from app.models.work import Work
from app.services.auth import create_user_session


def _headers(db, user: User) -> dict[str, str]:
    token, _ = create_user_session(db, user, ttl_minutes=60)
    db.commit()
    return {"Authorization": f"Bearer {token}"}


def _make_works(client, headers, n: int) -> None:
    for i in range(n):
        client.post("/api/v1/works", headers=headers, json={"canonical_title": f"Paper {i:02d}"})


# --- pagination envelope -------------------------------------------------------------------------
def test_pagination_envelope_shape_and_totals(client, auth_headers):
    h = auth_headers("editor")
    _make_works(client, h, 7)
    body = client.get("/api/v1/works", headers=h, params={"per_page": 3}).json()
    assert set(body) == {"items", "total", "page", "pages", "per_page"}
    assert body["total"] == 7
    assert body["pages"] == 3
    assert body["page"] == 1
    assert body["per_page"] == 3
    assert len(body["items"]) == 3


def test_pagination_page_navigation_and_last_page(client, auth_headers):
    h = auth_headers("editor")
    _make_works(client, h, 7)
    last = client.get("/api/v1/works", headers=h, params={"per_page": 3, "page": 3}).json()
    assert last["page"] == 3
    assert len(last["items"]) == 1  # 7 % 3


def test_pagination_page_is_clamped_into_range(client, auth_headers):
    h = auth_headers("editor")
    _make_works(client, h, 5)
    body = client.get("/api/v1/works", headers=h, params={"per_page": 2, "page": 99}).json()
    assert body["pages"] == 3
    assert body["page"] == 3  # clamped down to the last page
    assert len(body["items"]) == 1


def test_pagination_empty_result_has_one_page(client, auth_headers):
    body = client.get("/api/v1/works", headers=auth_headers("reader")).json()
    assert body["total"] == 0
    assert body["pages"] == 1
    assert body["page"] == 1


def test_per_page_override_beats_user_preference(client, auth_headers, db):
    h = auth_headers("editor")
    _make_works(client, h, 6)
    # Set a small preference, then override it per-request.
    client.patch("/api/v1/auth/me", headers=h, json={"papers_per_page": 2})
    body = client.get("/api/v1/works", headers=h, params={"per_page": 5}).json()
    assert body["per_page"] == 5
    assert len(body["items"]) == 5


def test_user_preference_is_the_default_page_size(client, auth_headers):
    h = auth_headers("editor")
    _make_works(client, h, 6)
    client.patch("/api/v1/auth/me", headers=h, json={"papers_per_page": 2})
    body = client.get("/api/v1/works", headers=h).json()
    assert body["per_page"] == 2
    assert len(body["items"]) == 2
    assert body["pages"] == 3


def test_global_max_clamps_effective_per_page(client, auth_headers):
    admin = auth_headers("admin")
    _make_works(client, admin, 4)
    client.patch("/api/v1/admin/app-config", headers=admin, json={"max_papers_per_page": 2})
    body = client.get("/api/v1/works", headers=admin, params={"per_page": 500}).json()
    assert body["per_page"] == 2  # clamped to the global maximum


# --- profile round-trip --------------------------------------------------------------------------
def test_profile_papers_per_page_round_trip_and_reset(client, auth_headers):
    h = auth_headers("reader")
    assert client.get("/api/v1/auth/me", headers=h).json()["papers_per_page"] is None
    client.patch("/api/v1/auth/me", headers=h, json={"papers_per_page": 42})
    assert client.get("/api/v1/auth/me", headers=h).json()["papers_per_page"] == 42
    # Null resets to the server default.
    client.patch("/api/v1/auth/me", headers=h, json={"papers_per_page": None})
    assert client.get("/api/v1/auth/me", headers=h).json()["papers_per_page"] is None


def test_profile_papers_per_page_rejects_below_one(client, auth_headers):
    h = auth_headers("reader")
    assert (
        client.patch("/api/v1/auth/me", headers=h, json={"papers_per_page": 0}).status_code == 422
    )


# --- admin global-max round-trip -----------------------------------------------------------------
def test_admin_app_config_round_trip(client, auth_headers):
    admin = auth_headers("admin")
    assert (
        client.get("/api/v1/admin/app-config", headers=admin).json()["max_papers_per_page"] == 500
    )
    updated = client.patch(
        "/api/v1/admin/app-config", headers=admin, json={"max_papers_per_page": 250}
    )
    assert updated.json()["max_papers_per_page"] == 250
    assert (
        client.get("/api/v1/admin/app-config", headers=admin).json()["max_papers_per_page"] == 250
    )


def test_admin_app_config_requires_admin(client, auth_headers):
    assert client.get("/api/v1/admin/app-config", headers=auth_headers("editor")).status_code == 403
    assert (
        client.patch(
            "/api/v1/admin/app-config",
            headers=auth_headers("editor"),
            json={"max_papers_per_page": 9},
        ).status_code
        == 403
    )


def test_admin_app_config_rejects_below_one(client, auth_headers):
    assert (
        client.patch(
            "/api/v1/admin/app-config",
            headers=auth_headers("admin"),
            json={"max_papers_per_page": 0},
        ).status_code
        == 422
    )


# --- D32 shelves/racks columns -------------------------------------------------------------------
def _row_for(body: dict, work_id: uuid.UUID) -> dict:
    return next(item for item in body["items"] if item["id"] == str(work_id))


def test_shelves_and_racks_columns_populated(client, auth_headers, db):
    admin = auth_headers("admin")
    work = Work(canonical_title="Filed paper")
    shelf = Shelf(name="Alpha", access_level="open")
    rack = Rack(name="Rack One", access_level="open")
    db.add_all([work, shelf, rack])
    db.commit()
    db.add(ShelfWork(shelf_id=shelf.id, work_id=work.id))
    db.add(RackShelf(rack_id=rack.id, shelf_id=shelf.id))
    db.commit()

    row = _row_for(
        client.get("/api/v1/works", headers=admin, params={"per_page": 500}).json(), work.id
    )
    assert [s["name"] for s in row["shelves"]] == ["Alpha"]
    assert [r["name"] for r in row["racks"]] == ["Rack One"]


def test_loose_paper_has_empty_shelves_and_racks(client, auth_headers, db):
    admin = auth_headers("admin")
    work = Work(canonical_title="Loose paper")
    db.add(work)
    db.commit()
    row = _row_for(
        client.get("/api/v1/works", headers=admin, params={"per_page": 500}).json(), work.id
    )
    assert row["shelves"] == []
    assert row["racks"] == []


def test_shelves_racks_are_see_filtered(client, db, make_user):
    """A reader sees a paper's public shelf/rack but never the name of a hidden (private) one."""
    reader = make_user("col-reader", role="reader")
    work = Work(canonical_title="Cross-filed")
    public_shelf = Shelf(name="PublicShelf", access_level="open")
    secret_shelf = Shelf(name="SecretShelf", access_level="private")
    public_rack = Rack(name="PublicRack", access_level="open")
    secret_rack = Rack(name="SecretRack", access_level="private")
    db.add_all([work, public_shelf, secret_shelf, public_rack, secret_rack])
    db.commit()
    # The paper is on both shelves (so it's visible via the public one); the public shelf sits in
    # both a public and a private rack.
    db.add(ShelfWork(shelf_id=public_shelf.id, work_id=work.id))
    db.add(ShelfWork(shelf_id=secret_shelf.id, work_id=work.id))
    db.add(RackShelf(rack_id=public_rack.id, shelf_id=public_shelf.id))
    db.add(RackShelf(rack_id=secret_rack.id, shelf_id=public_shelf.id))
    db.commit()

    body = client.get(
        "/api/v1/works", headers=_headers(db, reader), params={"per_page": 500}
    ).json()
    row = _row_for(body, work.id)
    shelf_names = {s["name"] for s in row["shelves"]}
    rack_names = {r["name"] for r in row["racks"]}
    assert "PublicShelf" in shelf_names
    assert "SecretShelf" not in shelf_names
    assert "PublicRack" in rack_names
    assert "SecretRack" not in rack_names


def test_app_config_citing_cap_and_ai_threshold_roundtrip(client, auth_headers):
    """S20/S16: the two new runtime knobs default correctly and round-trip via the admin API."""
    admin = auth_headers("owner")
    got = client.get("/api/v1/admin/app-config", headers=admin).json()
    assert got["citing_papers_fetch_cap"] == 1000
    assert got["ai_scope_job_threshold"] == 100
    patched = client.patch(
        "/api/v1/admin/app-config",
        headers=admin,
        json={"citing_papers_fetch_cap": 250, "ai_scope_job_threshold": 40},
    ).json()
    assert patched["citing_papers_fetch_cap"] == 250
    assert patched["ai_scope_job_threshold"] == 40
    assert (
        client.patch(
            "/api/v1/admin/app-config", headers=admin, json={"citing_papers_fetch_cap": 0}
        ).status_code
        == 422
    )
