"""D31.1 — audit-event wiring (§7.6), the append-only file sink, and the backup/restore CLI event."""

import json
import uuid
from pathlib import Path

import pytest
from app.models.audit import AuditEvent
from sqlalchemy import select


def _event_types(db, entity_id: str | None = None) -> list[str]:
    rows = db.scalars(select(AuditEvent)).all()
    if entity_id is not None:
        rows = [r for r in rows if str(r.entity_id) == entity_id]
    return [r.event_type for r in rows]


def test_shelf_create_and_update_emit_events(client, auth_headers, db) -> None:
    owner = auth_headers("owner")
    created = client.post("/api/v1/shelves", headers=owner, json={"name": "Shelf A"})
    assert created.status_code == 201
    shelf_id = created.json()["id"]
    patched = client.patch(f"/api/v1/shelves/{shelf_id}", headers=owner, json={"name": "Shelf B"})
    assert patched.status_code == 200
    types = _event_types(db, shelf_id)
    assert "shelf.created" in types
    assert "shelf.modified" in types


def test_rack_create_and_update_emit_events(client, auth_headers, db) -> None:
    owner = auth_headers("owner")
    created = client.post("/api/v1/racks", headers=owner, json={"name": "Rack A"})
    assert created.status_code == 201
    rack_id = created.json()["id"]
    patched = client.patch(f"/api/v1/racks/{rack_id}", headers=owner, json={"name": "Rack B"})
    assert patched.status_code == 200
    types = _event_types(db, rack_id)
    assert "rack.created" in types
    assert "rack.modified" in types


def test_work_metadata_edit_emits_event(client, auth_headers, db) -> None:
    editor = auth_headers("editor")
    work_id = client.post(
        "/api/v1/works", headers=editor, json={"canonical_title": "Original"}
    ).json()["id"]
    patched = client.patch(
        f"/api/v1/works/{work_id}", headers=editor, json={"canonical_title": "Edited"}
    )
    assert patched.status_code == 200
    assert "paper.metadata_edited" in _event_types(db, work_id)


def test_annotation_create_emits_event(client, auth_headers, db) -> None:
    editor = auth_headers("editor")
    work_id = client.post(
        "/api/v1/works", headers=editor, json={"canonical_title": "Annotated"}
    ).json()["id"]
    created = client.post(
        f"/api/v1/works/{work_id}/annotations",
        headers=editor,
        json={"annotation_type": "note", "content_markdown": "hello"},
    )
    assert created.status_code == 201
    assert "annotation.created" in _event_types(db)


def test_audit_file_sink_appends_json_lines(client, auth_headers, tmp_path) -> None:
    """Every recorded event is mirrored as one JSON line to the append-only sink."""
    owner = auth_headers("owner")
    client.post("/api/v1/shelves", headers=owner, json={"name": "Sink Shelf"})
    sink = Path(tmp_path) / "audit.jsonl"
    assert sink.exists()
    lines = [json.loads(line) for line in sink.read_text().splitlines() if line.strip()]
    assert any(entry["event_type"] == "shelf.created" for entry in lines)
    # Each line is a complete, self-describing record.
    for entry in lines:
        assert entry["id"]
        assert entry["created_at"]


def test_job_wrapper_emits_lifecycle_events(db, monkeypatch) -> None:
    """The RQ job wrapper emits job.started + job.completed around a successful job body."""
    import app.db.session as session_mod
    from app.workers.jobs import chunk_work_job

    def _factory():
        return type(db)(bind=db.get_bind())

    monkeypatch.setattr(session_mod, "SessionLocal", _factory)
    # Missing work → the body is a no-op, but the wrapper still records the lifecycle.
    chunk_work_job(str(uuid.uuid4()))
    db.expire_all()
    types = _event_types(db)
    assert "job.started" in types
    assert "job.completed" in types


def test_job_wrapper_emits_failed_event(db, monkeypatch) -> None:
    import app.db.session as session_mod
    from app.workers import jobs

    def _factory():
        return type(db)(bind=db.get_bind())

    monkeypatch.setattr(session_mod, "SessionLocal", _factory)

    @jobs._audited_job
    def _boom() -> None:
        raise RuntimeError("kaboom")

    with pytest.raises(RuntimeError):
        _boom()
    db.expire_all()
    assert "job.failed" in _event_types(db)


def test_backup_event_cli_records_event(db, monkeypatch) -> None:
    from scripts import record_backup_event

    def _factory():
        return type(db)(bind=db.get_bind())

    monkeypatch.setattr(record_backup_event, "SessionLocal", _factory)
    monkeypatch.setattr(record_backup_event.Base.metadata, "create_all", lambda **_: None)
    record_backup_event.record_backup_event("backup.created", artifact="db-x.sql.gz")
    db.expire_all()
    assert "backup.created" in _event_types(db)
