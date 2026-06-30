"""Find-on-web allowed download hosts management API (batch 2 #5 hardening).

Covers the admin-or-owner management endpoints: list merged hosts, add a DB host, remove a DB host,
and the auth matrix (owner + admin allowed; editor + reader → 403). Default hosts are non-removable.
"""

import uuid


def test_list_includes_defaults_and_db(client, auth_headers):
    owner = auth_headers("owner")
    created = client.post(
        "/api/v1/admin/web-find/allowed-hosts",
        headers=owner,
        json={"host": "repo.example.org"},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["source"] == "db"
    assert body["removable"] is True

    listed = client.get("/api/v1/admin/web-find/allowed-hosts", headers=owner)
    assert listed.status_code == 200
    by_host = {item["host"]: item for item in listed.json()}
    assert "arxiv.org" in by_host
    assert by_host["arxiv.org"]["source"] == "default"
    assert by_host["arxiv.org"]["removable"] is False
    assert "repo.example.org" in by_host
    assert by_host["repo.example.org"]["removable"] is True


def test_admin_can_manage(client, auth_headers):
    admin = auth_headers("admin")
    created = client.post(
        "/api/v1/admin/web-find/allowed-hosts",
        headers=admin,
        json={"host": "admin-added.example.org"},
    )
    assert created.status_code == 201, created.text
    host_id = created.json()["id"]
    removed = client.delete(f"/api/v1/admin/web-find/allowed-hosts/{host_id}", headers=admin)
    assert removed.status_code == 204


def test_add_validates_hostname(client, auth_headers):
    r = client.post(
        "/api/v1/admin/web-find/allowed-hosts",
        headers=auth_headers("owner"),
        json={"host": "not a host!"},
    )
    assert r.status_code == 400


def test_management_is_admin_or_owner_only(client, auth_headers):
    for role in ("editor", "reader"):
        headers = auth_headers(role)
        assert (
            client.get("/api/v1/admin/web-find/allowed-hosts", headers=headers).status_code == 403
        )
        assert (
            client.post(
                "/api/v1/admin/web-find/allowed-hosts",
                headers=headers,
                json={"host": f"r-{role}.example.org"},
            ).status_code
            == 403
        )
        assert (
            client.delete(
                f"/api/v1/admin/web-find/allowed-hosts/{uuid.uuid4()}", headers=headers
            ).status_code
            == 403
        )


def test_remove_missing_is_404(client, auth_headers):
    r = client.delete(
        f"/api/v1/admin/web-find/allowed-hosts/{uuid.uuid4()}", headers=auth_headers("admin")
    )
    assert r.status_code == 404
