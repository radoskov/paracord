"""Domain-error hierarchy + app-level handler (S4)."""

import uuid


def test_domain_errors_map_to_http_statuses(client, auth_headers) -> None:
    """A service-raised NotFoundError surfaces as the same 404 the old HTTPException produced."""
    work = client.post(
        "/api/v1/works", headers=auth_headers("owner"), json={"canonical_title": "T"}
    ).json()
    missing_shelf = uuid.uuid4()
    resp = client.post(
        f"/api/v1/shelves/{missing_shelf}/works",
        headers=auth_headers("owner"),
        json={"work_id": work["id"]},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Shelf not found"


def test_domain_error_status_codes() -> None:
    from app.errors import ConflictError, DomainError, NotFoundError, PermissionDeniedError

    assert NotFoundError.status_code == 404
    assert ConflictError.status_code == 409
    assert PermissionDeniedError.status_code == 403
    assert issubclass(NotFoundError, DomainError)
