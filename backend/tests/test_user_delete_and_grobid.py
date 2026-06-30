"""Admin user deletion (disable→delete) + GROBID multipart form shape."""


def _create_user(client, owner, username):
    return client.post(
        "/api/v1/admin/users",
        headers=owner,
        json={
            "username": username,
            "password": "pw-fake-aaaa",
            "role": "reader",
        },  # pragma: allowlist secret
    ).json()


def test_delete_requires_disable_first(client, auth_headers):
    owner = auth_headers("owner")
    user = _create_user(client, owner, "doomed")
    uid = user["id"]

    # Active user cannot be deleted.
    blocked = client.delete(f"/api/v1/admin/users/{uid}", headers=owner)
    assert blocked.status_code == 400

    # Disable, then delete succeeds.
    assert client.post(f"/api/v1/admin/users/{uid}/disable", headers=owner).status_code == 200
    deleted = client.delete(f"/api/v1/admin/users/{uid}", headers=owner)
    assert deleted.status_code == 204

    # Gone from the list, and a re-delete is 404.
    users = client.get("/api/v1/admin/users", headers=owner).json()
    assert all(u["id"] != uid for u in users)
    assert client.delete(f"/api/v1/admin/users/{uid}", headers=owner).status_code == 404


def test_delete_user_is_owner_only(client, auth_headers):
    owner = auth_headers("owner")
    uid = _create_user(client, owner, "victim")["id"]
    client.post(f"/api/v1/admin/users/{uid}/disable", headers=owner)
    assert (
        client.delete(f"/api/v1/admin/users/{uid}", headers=auth_headers("editor")).status_code
        == 403
    )


def test_grobid_form_data_repeats_coordinates_as_list():
    from app.core.config import Settings
    from app.services.grobid_client import GrobidClient

    settings = Settings(grobid_coordinate_elements=["ref", "biblStruct", "s"])
    data = GrobidClient("http://grobid:8070", settings=settings)._form_data()
    # A dict (not a list of tuples) with the repeated field as a list — the shape httpx2 needs.
    assert isinstance(data, dict)
    assert data["teiCoordinates"] == ["ref", "biblStruct", "s"]
    assert data["consolidateHeader"] in ("0", "1")
