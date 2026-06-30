"""Stage 7: selection-scope export + view audit events (§7.6, §8.17)."""

from app.models.audit import AuditEvent
from sqlalchemy import select


def _make_work(client, headers, title: str) -> str:
    return client.post("/api/v1/works", headers=headers, json={"canonical_title": title}).json()[
        "id"
    ]


def test_selection_export_returns_only_chosen_works(client, auth_headers):
    editor = auth_headers("editor")
    a = _make_work(client, editor, "Selected Paper Alpha")
    b = _make_work(client, editor, "Selected Paper Beta")
    _make_work(client, editor, "Unselected Paper Gamma")

    r = client.post(
        "/api/v1/exports",
        headers=editor,
        json={"scope_type": "selection", "work_ids": [a, b], "format": "bibtex"},
    )
    assert r.status_code == 200
    content = r.json()["content"]
    assert "Selected Paper Alpha" in content
    assert "Selected Paper Beta" in content
    assert "Unselected Paper Gamma" not in content


def test_selection_export_requires_work_ids(client, auth_headers):
    r = client.post(
        "/api/v1/exports",
        headers=auth_headers("editor"),
        json={"scope_type": "selection", "format": "bibtex"},
    )
    assert r.status_code == 400


def test_styled_export_apa_and_ieee(client, auth_headers):
    editor = auth_headers("editor")
    wid = _make_work(client, editor, "Deep Nets")
    apa = client.post(
        "/api/v1/exports",
        headers=editor,
        json={"scope_type": "selection", "work_ids": [wid], "format": "styled", "style": "apa"},
    )
    assert apa.status_code == 200
    assert "Deep Nets" in apa.json()["content"]
    ieee = client.post(
        "/api/v1/exports",
        headers=editor,
        json={"scope_type": "selection", "work_ids": [wid], "format": "styled", "style": "ieee"},
    )
    assert ieee.json()["content"].lstrip().startswith("[1]")


def test_library_scope_export(client, auth_headers):
    editor = auth_headers("editor")
    _make_work(client, editor, "Library Wide Paper")
    r = client.post(
        "/api/v1/exports", headers=editor, json={"scope_type": "library", "format": "bibtex"}
    )
    assert r.status_code == 200
    assert "Library Wide Paper" in r.json()["content"]


def test_get_work_records_paper_viewed(client, auth_headers, db):
    editor = auth_headers("editor")
    work_id = _make_work(client, editor, "Viewed Paper")
    assert client.get(f"/api/v1/works/{work_id}", headers=auth_headers("reader")).status_code == 200
    events = db.scalars(select(AuditEvent).where(AuditEvent.event_type == "paper.viewed")).all()
    assert any(str(e.entity_id) == work_id for e in events)
