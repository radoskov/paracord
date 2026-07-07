"""AuthZ / IDOR fuzzing (Batch S): a plain reader must never read or mutate a work they cannot see
— including through the newer viz / citation / neighborhood surfaces — nor another user's private
per-user data (worklist, import batch). Hidden works must yield 403/404 with no visibility leak, and
mass-assignment of privileged fields must be ignored.
"""

from __future__ import annotations

import uuid

import pytest
from app.models.citation import Reference

pytestmark = pytest.mark.safety


# --- hidden-work reads: every work-addressing endpoint must 404 ---------------------------------


def test_get_hidden_work_404(client, auth_headers, hidden_work) -> None:
    work = hidden_work()
    resp = client.get(f"/api/v1/works/{work.id}", headers=auth_headers("reader"))
    assert resp.status_code == 404


def test_hidden_work_citation_neighborhood_404(client, auth_headers, hidden_work) -> None:
    work = hidden_work()
    resp = client.get(
        f"/api/v1/works/{work.id}/citation-neighborhood", headers=auth_headers("reader")
    )
    assert resp.status_code == 404


def test_hidden_work_shelves_404(client, auth_headers, hidden_work) -> None:
    work = hidden_work()
    resp = client.get(f"/api/v1/works/{work.id}/shelves", headers=auth_headers("reader"))
    assert resp.status_code == 404


def test_hidden_work_related_404(client, auth_headers, hidden_work) -> None:
    work = hidden_work()
    resp = client.get(f"/api/v1/works/{work.id}/related", headers=auth_headers("reader"))
    assert resp.status_code == 404


def test_hidden_work_references_404(client, auth_headers, hidden_work) -> None:
    work = hidden_work()
    resp = client.get(f"/api/v1/works/{work.id}/references", headers=auth_headers("reader"))
    assert resp.status_code == 404


def test_hidden_work_metadata_404(client, auth_headers, hidden_work) -> None:
    work = hidden_work()
    resp = client.get(f"/api/v1/works/{work.id}/metadata", headers=auth_headers("reader"))
    assert resp.status_code == 404


# --- hidden-work mutations: a reader (and a non-owning contributor) must 403/404 ----------------


def test_reader_cannot_patch_hidden_work(client, auth_headers, hidden_work) -> None:
    work = hidden_work()
    resp = client.patch(
        f"/api/v1/works/{work.id}",
        headers=auth_headers("reader"),
        json={"canonical_title": "pwned"},
    )
    assert resp.status_code in (403, 404)


def test_reader_cannot_delete_hidden_work(client, auth_headers, hidden_work) -> None:
    work = hidden_work()
    resp = client.delete(f"/api/v1/works/{work.id}", headers=auth_headers("reader"))
    assert resp.status_code in (403, 404)


# --- viz + citation scope containers: a private shelf/rack scope must 404 -----------------------


@pytest.mark.parametrize("view_type", ["temporal_map", "co_citation", "topic_river"])
def test_viz_private_shelf_scope_404(client, auth_headers, make_shelf, view_type: str) -> None:
    private = make_shelf(access_level="private")
    resp = client.get(
        f"/api/v1/viz/{view_type}",
        headers=auth_headers("reader"),
        params={"scope_type": "shelf", "scope_id": str(private.id)},
    )
    assert resp.status_code == 404


def test_viz_private_rack_scope_404(client, auth_headers, make_rack) -> None:
    private = make_rack(access_level="private")
    resp = client.get(
        "/api/v1/viz/temporal_map",
        headers=auth_headers("reader"),
        params={"scope_type": "rack", "scope_id": str(private.id)},
    )
    assert resp.status_code == 404


def test_viz_rejects_unknown_scope(client, auth_headers) -> None:
    resp = client.get(
        "/api/v1/viz/temporal_map",
        headers=auth_headers("reader"),
        params={"scope_type": "not_a_scope"},
    )
    assert resp.status_code == 400


def test_citation_summary_private_shelf_scope_404(client, auth_headers, make_shelf) -> None:
    private = make_shelf(access_level="private")
    resp = client.get(
        "/api/v1/citations/summary",
        headers=auth_headers("reader"),
        params={"scope_type": "shelf", "scope_id": str(private.id)},
    )
    assert resp.status_code == 404


def test_missing_export_private_shelf_scope_404(client, auth_headers, make_shelf) -> None:
    private = make_shelf(access_level="private")
    resp = client.get(
        "/api/v1/citations/missing-export",
        headers=auth_headers("reader"),
        params={"scope_type": "shelf", "scope_id": str(private.id), "format": "bibtex"},
    )
    assert resp.status_code == 404


# --- external-preview: a reference whose citing work is hidden must 404 -------------------------


def test_external_preview_hidden_reference_404(client, auth_headers, db, hidden_work) -> None:
    work = hidden_work()
    reference = Reference(citing_work_id=work.id, doi="10.1234/secret", title="Hidden ref")
    db.add(reference)
    db.commit()
    db.refresh(reference)
    resp = client.get(
        "/api/v1/citations/external-preview",
        headers=auth_headers("reader"),
        params={"reference_id": str(reference.id)},
    )
    assert resp.status_code == 404


def test_external_preview_unknown_reference_404(client, auth_headers) -> None:
    resp = client.get(
        "/api/v1/citations/external-preview",
        headers=auth_headers("reader"),
        params={"reference_id": str(uuid.uuid4())},
    )
    assert resp.status_code == 404


# --- per-user data isolation: worklist + import batches -----------------------------------------


def test_worklist_is_per_user(client, auth_headers) -> None:
    alice = auth_headers("editor", username="alice-wl")
    bob = auth_headers("editor", username="bob-wl")
    assert (
        client.put(
            "/api/v1/citations/worklist",
            headers=alice,
            json={"key": "some-missing-key", "decision": "import"},
        ).status_code
        == 200
    )
    # Bob must not see Alice's decision.
    bob_decisions = client.get("/api/v1/citations/worklist", headers=bob).json()["decisions"]
    assert "some-missing-key" not in bob_decisions


def test_import_batch_of_other_user_is_404(client, auth_headers, db) -> None:
    from app.models.source import ImportBatch

    alice = client  # reuse client
    alice_headers = auth_headers("editor", username="alice-batch")
    # Create a batch owned by Alice directly.
    me = client.get("/api/v1/auth/me", headers=alice_headers).json()
    batch = ImportBatch(
        input_type="bibtex", status="completed", created_by_user_id=uuid.UUID(me["id"])
    )
    db.add(batch)
    db.commit()
    db.refresh(batch)
    bob_headers = auth_headers("editor", username="bob-batch")
    resp = alice.get(f"/api/v1/imports/{batch.id}", headers=bob_headers)
    assert resp.status_code == 404


# --- privileged controls need the right floor --------------------------------------------------


@pytest.mark.parametrize("role", ["reader", "contributor", "editor"])
def test_app_config_patch_requires_admin(client, auth_headers, role: str) -> None:
    resp = client.patch(
        "/api/v1/admin/app-config", headers=auth_headers(role), json={"max_queue_len": 5}
    )
    assert resp.status_code == 403


@pytest.mark.parametrize("role", ["reader", "contributor"])
def test_clear_queue_requires_editor_floor(client, auth_headers, role: str) -> None:
    assert client.post("/api/v1/jobs/clear", headers=auth_headers(role)).status_code == 403


@pytest.mark.parametrize("role", ["reader", "editor", "librarian"])
def test_reset_workers_requires_admin(client, auth_headers, role: str) -> None:
    assert client.post("/api/v1/jobs/reset-workers", headers=auth_headers(role)).status_code == 403


def test_theme_delete_requires_admin(client, auth_headers) -> None:
    assert (
        client.delete("/api/v1/admin/themes/some-slug", headers=auth_headers("reader")).status_code
        == 403
    )


# --- mass assignment: privileged fields on work-create must be ignored --------------------------


def test_work_create_ignores_mass_assigned_fields(client, auth_headers, db) -> None:
    from app.models.user import User

    headers = auth_headers("contributor", username="ma-contrib")
    me = client.get("/api/v1/auth/me", headers=headers).json()
    victim = db.scalar(__import__("sqlalchemy").select(User).where(User.username == "ma-contrib"))
    injected_id = str(uuid.uuid4())
    resp = client.post(
        "/api/v1/works",
        headers=headers,
        json={
            "canonical_title": "mine",
            "id": injected_id,
            "created_by_user_id": str(uuid.uuid4()),
            "role": "owner",
            "is_bootstrap": True,
            "user_confirmed": False,
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    # The server owns id + created_by; the injected values are ignored.
    assert body["id"] != injected_id
    assert body["created_by_user_id"] == me["id"]
    # The actor's own role is unchanged by the injected role field.
    db.refresh(victim)
    assert victim.role == "contributor"
