"""Privilege-escalation / role-bypass probes (Batch S): the role ladder must hold over HTTP, an
admin must not be able to manage other admins or the owner, and privileged fields on the admin
user-management surface must not be mass-assignable.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

pytestmark = pytest.mark.safety


# --- role ladder floors over HTTP ---------------------------------------------------------------


def test_reader_cannot_create_work(client, auth_headers) -> None:
    resp = client.post(
        "/api/v1/works", headers=auth_headers("reader"), json={"canonical_title": "x"}
    )
    assert resp.status_code == 403


@pytest.mark.parametrize("role", ["reader", "contributor", "editor"])
def test_below_librarian_cannot_create_shelf(client, auth_headers, role: str) -> None:
    resp = client.post("/api/v1/shelves", headers=auth_headers(role), json={"name": "s"})
    assert resp.status_code == 403


def test_librarian_can_create_shelf(client, auth_headers) -> None:
    resp = client.post("/api/v1/shelves", headers=auth_headers("librarian"), json={"name": "s"})
    assert resp.status_code == 201


@pytest.mark.parametrize("role", ["reader", "contributor", "editor", "librarian"])
def test_below_admin_cannot_list_users(client, auth_headers, role: str) -> None:
    assert client.get("/api/v1/admin/users", headers=auth_headers(role)).status_code == 403


# --- admin cannot manage admins or the owner ---------------------------------------------------


def test_admin_cannot_create_admin(client, auth_headers) -> None:
    resp = client.post(
        "/api/v1/admin/users",
        headers=auth_headers("admin"),
        json={"username": "new-admin", "password": "test-pass-1234", "role": "admin"},
    )
    assert resp.status_code == 403


def test_admin_cannot_promote_user_to_admin(client, auth_headers, db, make_user) -> None:
    victim = make_user("promote-me", role="reader")
    resp = client.patch(
        f"/api/v1/admin/users/{victim.id}", headers=auth_headers("admin"), json={"role": "admin"}
    )
    assert resp.status_code == 403
    db.refresh(victim)
    assert victim.role == "reader"


def test_admin_cannot_role_change_the_owner(client, auth_headers, db) -> None:
    from app.models.user import User

    owner_headers = auth_headers("owner")  # ensures an owner row exists
    assert owner_headers
    owner = db.scalar(select(User).where(User.role == "owner"))
    resp = client.patch(
        f"/api/v1/admin/users/{owner.id}", headers=auth_headers("admin"), json={"role": "reader"}
    )
    assert resp.status_code in (400, 403)
    db.refresh(owner)
    assert owner.role == "owner"


def test_admin_cannot_disable_another_admin(client, auth_headers, db, make_user) -> None:
    other_admin = make_user("other-admin", role="admin")
    resp = client.post(
        f"/api/v1/admin/users/{other_admin.id}/disable", headers=auth_headers("admin")
    )
    assert resp.status_code == 403
    db.refresh(other_admin)
    assert other_admin.disabled_at is None


def test_admin_cannot_delete_the_owner(client, auth_headers, db) -> None:
    from app.models.user import User

    assert auth_headers("owner")
    owner = db.scalar(select(User).where(User.role == "owner"))
    resp = client.delete(f"/api/v1/admin/users/{owner.id}", headers=auth_headers("admin"))
    assert resp.status_code in (400, 403)
    assert db.get(User, owner.id) is not None


# --- self-escalation attempts ------------------------------------------------------------------


def test_profile_update_cannot_change_own_role(client, auth_headers, db) -> None:
    from app.models.user import User

    headers = auth_headers("reader", username="self-escalate")
    resp = client.patch("/api/v1/auth/me", headers=headers, json={"role": "owner"})
    # The profile schema has no role field; the attempt is ignored (200) or rejected (422).
    assert resp.status_code in (200, 422)
    victim = db.scalar(select(User).where(User.username == "self-escalate"))
    assert victim.role == "reader"


def test_create_user_ignores_mass_assigned_bootstrap_and_id(client, auth_headers, db) -> None:
    from app.models.user import User

    injected_id = str(uuid.uuid4())
    resp = client.post(
        "/api/v1/admin/users",
        headers=auth_headers("owner"),
        json={
            "username": "ma-user",
            "password": "test-pass-1234",
            "role": "reader",
            "id": injected_id,
            "is_bootstrap": True,
        },
    )
    assert resp.status_code == 201
    created = db.scalar(select(User).where(User.username == "ma-user"))
    assert str(created.id) != injected_id
    assert created.is_bootstrap is False
