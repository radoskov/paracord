"""Import-missing-reference (§8.9) + export citation-key overrides (§8.17.3)."""

from app.models.citation import Reference
from app.models.work import Work


def test_import_reference_as_work_resolves_it(client, auth_headers, db):
    citing = Work(canonical_title="Citing", normalized_title="citing")
    db.add(citing)
    db.flush()
    ref = Reference(
        citing_work_id=citing.id,
        title="A Cited Paper Not Yet In Library",
        doi="10.9999/cited",
        year=2018,
    )
    db.add(ref)
    db.commit()

    created = client.post(f"/api/v1/works/from-reference/{ref.id}", headers=auth_headers("editor"))
    assert created.status_code == 201
    body = created.json()
    assert body["canonical_title"] == "A Cited Paper Not Yet In Library"

    # Reference now resolves to the new work (idempotent: a second call returns the same work).
    db.expire_all()
    refreshed = db.get(Reference, ref.id)
    assert str(refreshed.resolved_work_id) == body["id"]
    assert refreshed.resolution_status == "local_match"
    again = client.post(f"/api/v1/works/from-reference/{ref.id}", headers=auth_headers("editor"))
    assert again.json()["id"] == body["id"]


def test_export_citation_key_override(client, auth_headers):
    h = auth_headers("editor")
    wid = client.post(
        "/api/v1/works", headers=h, json={"canonical_title": "Keyed Paper", "year": 2020}
    ).json()["id"]
    r = client.post(
        "/api/v1/exports",
        headers=h,
        json={
            "scope_type": "selection",
            "work_ids": [wid],
            "format": "bibtex",
            "citation_keys": {wid: "myCustomKey2020"},
        },
    )
    assert r.status_code == 200
    assert "myCustomKey2020" in r.json()["content"]
