"""Security-focused API tests (the spec's non-negotiables, exercised over HTTP).

Covers: authentication required everywhere, role-based authorization, no guest role,
account-enumeration resistance, audit logging of auth, and filesystem-isolation on the
PDF stream endpoint (no path escapes).
"""

import pytest
from app.core.security import assert_no_guest_roles
from app.models.file import File, Location
from app.models.source import Source

PROTECTED_GET_ENDPOINTS = [
    "/api/v1/works",
    "/api/v1/shelves",
    "/api/v1/racks",
    "/api/v1/tags",
    "/api/v1/files",
    "/api/v1/sources",
    "/api/v1/admin/users",
    "/api/v1/admin/audit-events",
]


def test_protected_endpoints_require_authentication(client):
    for path in PROTECTED_GET_ENDPOINTS:
        assert client.get(path).status_code == 401, f"{path} should require auth"


def test_health_is_public(client):
    assert client.get("/api/v1/health").status_code == 200


def test_write_role_matrix(client, auth_headers):
    reader, editor, owner = auth_headers("reader"), auth_headers("editor"), auth_headers("owner")
    # Editors (and owners) can write library content; readers cannot.
    assert client.post("/api/v1/shelves", headers=reader, json={"name": "r"}).status_code == 403
    assert client.post("/api/v1/shelves", headers=editor, json={"name": "e"}).status_code == 201
    assert client.post("/api/v1/shelves", headers=owner, json={"name": "o"}).status_code == 201
    # User management requires owner or admin; editors/readers are rejected.
    payload = {"username": "newbie", "password": "test-pass-1234", "role": "reader"}
    assert client.post("/api/v1/admin/users", headers=editor, json=payload).status_code == 403
    assert client.post("/api/v1/admin/users", headers=owner, json=payload).status_code == 201


def test_admin_role_management_matrix_over_http(client, auth_headers, make_user):
    """End-to-end (#20): admins administer editors/readers but never admins/the owner."""
    owner = auth_headers("owner")
    admin = auth_headers("admin")

    # Admins can create readers/editors.
    r = client.post(
        "/api/v1/admin/users",
        headers=admin,
        json={"username": "by-admin", "password": "test-pass-1234", "role": "editor"},
    )
    assert r.status_code == 201

    # Admins cannot create another admin (owner-only).
    r = client.post(
        "/api/v1/admin/users",
        headers=admin,
        json={"username": "rogue-admin", "password": "test-pass-1234", "role": "admin"},
    )
    assert r.status_code == 403

    # The owner can create an admin; that admin cannot then be managed by a different admin.
    target_admin = make_user("target-admin", role="admin")
    assert (
        client.post(f"/api/v1/admin/users/{target_admin.id}/disable", headers=admin).status_code
        == 403
    )
    assert (
        client.patch(
            f"/api/v1/admin/users/{target_admin.id}", headers=admin, json={"role": "reader"}
        ).status_code
        == 403
    )
    # ...but the owner can.
    assert (
        client.patch(
            f"/api/v1/admin/users/{target_admin.id}", headers=owner, json={"role": "reader"}
        ).status_code
        == 200
    )

    # The owner role can never be created or assigned (schema rejects unknown enum value
    # downgrades to a 4xx; the explicit owner value is rejected by the service as 400).
    assert (
        client.post(
            "/api/v1/admin/users",
            headers=owner,
            json={"username": "second-owner", "password": "test-pass-1234", "role": "owner"},
        ).status_code
        == 400
    )


def test_owner_account_is_locked_over_http(client, auth_headers, make_user, db):
    """The owner (any owner-role account) cannot be disabled/role-changed by anyone, incl. self."""
    owner_user = make_user("the-owner", role="owner")
    from app.services.auth import create_user_session

    token, _ = create_user_session(db, owner_user, ttl_minutes=60)
    db.commit()
    owner = {"Authorization": f"Bearer {token}"}

    # Self-disable is blocked.
    assert (
        client.post(f"/api/v1/admin/users/{owner_user.id}/disable", headers=owner).status_code
        == 400
    )
    # A second owner-role account cannot be disabled either.
    other = make_user("other-owner", role="owner")
    assert client.post(f"/api/v1/admin/users/{other.id}/disable", headers=owner).status_code == 403
    # ...nor role-changed.
    assert (
        client.patch(
            f"/api/v1/admin/users/{other.id}", headers=owner, json={"role": "reader"}
        ).status_code
        == 403
    )


def test_no_guest_role_assertion():
    assert_no_guest_roles(["owner", "editor", "reader"])  # ok
    with pytest.raises(ValueError):
        assert_no_guest_roles(["owner", "guest"])


def test_guest_role_rejected_by_api(client, auth_headers):
    # role is a closed enum; an attempt to create a guest account is a schema error.
    r = client.post(
        "/api/v1/admin/users",
        headers=auth_headers("owner"),
        json={"username": "g", "password": "test-pass-1234", "role": "guest"},
    )
    assert r.status_code == 422


def test_login_does_not_leak_account_existence(client, make_user):
    make_user("real", role="owner")
    bad_pw = client.post("/api/v1/auth/login", json={"username": "real", "password": "wrong"})
    unknown = client.post("/api/v1/auth/login", json={"username": "ghost", "password": "wrong"})
    assert bad_pw.status_code == unknown.status_code == 401
    assert bad_pw.json()["detail"] == unknown.json()["detail"]


def test_login_writes_audit_events(client, auth_headers, make_user, default_password):
    make_user("auditor", role="owner")
    client.post("/api/v1/auth/login", json={"username": "auditor", "password": default_password})
    client.post("/api/v1/auth/login", json={"username": "auditor", "password": "wrong"})
    events = client.get("/api/v1/admin/audit-events", headers=auth_headers("owner")).json()
    types = {e["event_type"] for e in events["items"]}
    assert {"auth.login_success", "auth.login_failure"} <= types


def test_stream_rejects_location_outside_configured_root(client, auth_headers, db, tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "secret.pdf"
    outside.write_bytes(b"%PDF-1.4\n")
    source = Source(type="server_folder", name="S", path_alias="s", config={"root_path": str(root)})
    file = File(sha256="c" * 64, size_bytes=10, mime_type="application/pdf")
    db.add_all([source, file])
    db.flush()
    db.add(
        Location(
            file_id=file.id,
            source_id=source.id,
            location_type="server_path",
            internal_uri=str(outside),
            is_available=True,
            is_primary=True,
        )
    )
    db.commit()
    r = client.get(f"/api/v1/files/{file.id}/stream", headers=auth_headers("reader"))
    assert r.status_code == 403
