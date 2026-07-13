"""Version-tolerant export/backup + restore (feature batch 2026-07-13, item 1)."""

import hashlib
import io
import json
import uuid
import zipfile

import pytest
from app.core.config import get_settings
from app.models.file import File, FileWorkLink
from app.models.organization import Shelf, ShelfWork
from app.models.work import Work
from app.services import backup as backup_service
from sqlalchemy import func, select, text


@pytest.fixture()
def storage_tmp(tmp_path, monkeypatch):
    """Isolate managed library + backups dir under tmp_path."""
    monkeypatch.setattr(get_settings(), "managed_library_root", str(tmp_path / "library"))
    return tmp_path


def _seed(db) -> Work:
    work = Work(
        canonical_title="Backed Up Paper", normalized_title="backed up paper", doi="10.1/bk"
    )
    db.add(work)
    db.flush()
    shelf = Shelf(name="Backup Shelf")
    db.add(shelf)
    db.flush()
    db.add(ShelfWork(shelf_id=shelf.id, work_id=work.id))
    db.commit()
    return work


def test_export_restore_round_trip_replace(db, storage_tmp) -> None:
    work = _seed(db)
    result = backup_service.create_backup(db, include_pdfs=False)
    db.commit()
    assert result["manifest"]["tables"]["works"] == 1
    assert result["manifest"]["hash_algorithm"] == "sha256"

    # Mutate the live data, then replace-restore: the backup state wins.
    work.canonical_title = "Mutated After Backup"
    extra = Work(canonical_title="Post-backup Paper", normalized_title="post backup paper")
    db.add(extra)
    db.commit()

    summary = backup_service.restore_backup(
        db, path=backup_service.backups_dir() / result["archive"], mode="replace"
    )
    db.commit()
    assert summary["tables"]["works"]["inserted"] == 1
    titles = set(db.scalars(select(Work.canonical_title)).all())
    assert titles == {"Backed Up Paper"}  # mutation reverted, post-backup row gone
    assert db.scalar(select(func.count()).select_from(ShelfWork)) == 1


def test_merge_restores_only_missing_rows(db, storage_tmp) -> None:
    work = _seed(db)
    result = backup_service.create_backup(db, include_pdfs=False)
    db.commit()
    # Delete the shelf link + add an unrelated work; merge should re-add the link, keep the rest.
    db.execute(ShelfWork.__table__.delete())
    keeper = Work(canonical_title="Keeper", normalized_title="keeper")
    db.add(keeper)
    db.commit()

    summary = backup_service.restore_backup(
        db, path=backup_service.backups_dir() / result["archive"], mode="merge"
    )
    db.commit()
    assert summary["tables"]["works"]["skipped_existing"] == 1  # the backed-up work already exists
    assert summary["tables"]["shelf_works"]["inserted"] == 1  # the deleted link came back
    titles = set(db.scalars(select(Work.canonical_title)).all())
    assert {"Backed Up Paper", "Keeper"} <= titles
    assert work.id in set(db.scalars(select(Work.id)).all())


def test_restore_tolerates_schema_drift(db, storage_tmp) -> None:
    """Unknown columns are dropped, unknown tables ignored, missing columns backfilled."""
    archive_path = backup_service.backups_dir() / "drifted.zip"
    work_id = str(uuid.uuid4())
    with zipfile.ZipFile(archive_path, "w") as z:
        z.writestr(
            "tables/works.jsonl",
            json.dumps(
                {
                    "id": work_id,
                    "canonical_title": "From The Future",
                    "normalized_title": "from the future",
                    # Columns this version has never heard of:
                    "hologram_url": "future://x",
                    "sentiment_score": 0.9,
                    # NOTE: most current columns are absent → defaults must backfill.
                }
            )
            + "\n",
        )
        z.writestr("tables/quantum_flux.jsonl", json.dumps({"id": 1}) + "\n")
        z.writestr("manifest.json", json.dumps({"format_version": 1, "tables": {"works": 1}}))

    report = backup_service.analyze_backup(archive_path)
    works_entry = next(e for e in report["tables"] if e["table"] == "works")
    assert "hologram_url" in works_entry["dropped_columns"]
    flux = next(e for e in report["tables"] if e["table"] == "quantum_flux")
    assert flux["unknown_table"] is True

    summary = backup_service.restore_backup(db, path=archive_path, mode="merge")
    db.commit()
    assert summary["tables"]["works"]["inserted"] == 1
    assert "hologram_url" in summary["tables"]["works"]["dropped_columns"]
    restored = db.get(Work, uuid.UUID(work_id))
    assert restored.canonical_title == "From The Future"
    assert restored.reading_status is not None  # backfilled by the model default
    assert "quantum_flux" not in summary["tables"]  # unknown table ignored entirely


def test_rename_map_translates_old_columns(db, storage_tmp, monkeypatch) -> None:
    monkeypatch.setitem(backup_service._RENAMES, "works", {"old_title": "canonical_title"})
    archive_path = backup_service.backups_dir() / "renamed.zip"
    work_id = str(uuid.uuid4())
    with zipfile.ZipFile(archive_path, "w") as z:
        z.writestr(
            "tables/works.jsonl",
            json.dumps(
                {"id": work_id, "old_title": "Renamed Title", "normalized_title": "renamed title"}
            )
            + "\n",
        )
        z.writestr("manifest.json", json.dumps({"format_version": 1, "tables": {"works": 1}}))
    summary = backup_service.restore_backup(db, path=archive_path, mode="merge")
    db.commit()
    assert summary["tables"]["works"]["inserted"] == 1
    assert db.get(Work, uuid.UUID(work_id)).canonical_title == "Renamed Title"


def test_pdf_pairing_drops_unmatched_files_and_pairs_matched(db, storage_tmp) -> None:
    """A restored File with a PDF in the archive is paired into the managed library; a File whose
    sha256 matches nothing is deleted with its links (the paper survives)."""
    pdf_bytes = b"%PDF-1.4 fake"
    sha_present = hashlib.sha256(pdf_bytes).hexdigest()
    sha_missing = "ab" * 32

    work = _seed(db)
    present = File(sha256=sha_present, size_bytes=len(pdf_bytes), status="available")
    missing = File(sha256=sha_missing, size_bytes=3, status="available")
    db.add_all([present, missing])
    db.flush()
    db.add_all(
        [
            FileWorkLink(file_id=present.id, work_id=work.id),
            FileWorkLink(file_id=missing.id, work_id=work.id),
        ]
    )
    db.commit()

    result = backup_service.create_backup(db, include_pdfs=False)
    db.commit()
    archive_path = backup_service.backups_dir() / result["archive"]
    # Add the one PDF we do have to the archive (as an include_pdfs export would).
    with zipfile.ZipFile(archive_path, "a") as z:
        z.writestr(f"pdfs/{sha_present}.pdf", pdf_bytes)

    summary = backup_service.restore_backup(db, path=archive_path, mode="replace")
    db.commit()
    assert summary["files_paired"] == 1
    assert summary["files_dropped"] == 1
    assert summary["files_dropped_sha256"] == [sha_missing]
    remaining = set(db.scalars(select(File.sha256)).all())
    assert remaining == {sha_present}
    # The paper survives; only the unmatched file link is gone.
    assert db.scalar(select(func.count()).select_from(Work)) == 1
    assert db.scalar(select(func.count()).select_from(FileWorkLink)) == 1
    # The paired PDF landed content-addressed in the managed library.
    from pathlib import Path

    from app.services.storage import content_addressed_path

    managed = Path(get_settings().managed_library_root).expanduser().resolve()
    assert content_addressed_path(managed, sha_present).exists()


def test_replace_reinserts_owner_when_backup_has_none(db, storage_tmp, make_user) -> None:
    owner = make_user("the-owner", role="owner")
    db.commit()
    archive_path = backup_service.backups_dir() / "no-owner.zip"
    with zipfile.ZipFile(archive_path, "w") as z:
        z.writestr("tables/works.jsonl", "")
        z.writestr("manifest.json", json.dumps({"format_version": 1, "tables": {}}))
    summary = backup_service.restore_backup(db, path=archive_path, mode="replace")
    db.commit()
    assert summary["owner_reinserted"] is True
    row = db.execute(text("SELECT username, role FROM users WHERE role = 'owner'")).first()
    assert row is not None and row.username == owner.username


# --- endpoints: permissions + confirmation -----------------------------------------------------


def test_restore_is_owner_only_and_replace_needs_confirmation(
    client, auth_headers, db, storage_tmp, monkeypatch
) -> None:
    result = backup_service.create_backup(db, include_pdfs=False)
    db.commit()
    name = result["archive"]

    # Admin can create/list/download but NOT restore.
    listing = client.get("/api/v1/admin/backups", headers=auth_headers("owner")).json()
    assert any(b["archive"] == name for b in listing["backups"])
    assert (
        client.post(
            f"/api/v1/admin/backups/{name}/restore",
            headers=auth_headers("editor"),
            json={"mode": "merge"},
        ).status_code
        == 403
    )

    # Replace without the typed confirmation is refused.
    resp = client.post(
        f"/api/v1/admin/backups/{name}/restore",
        headers=auth_headers("owner"),
        json={"mode": "replace"},
    )
    assert resp.status_code == 400
    assert "REPLACE" in resp.json()["detail"]

    # Merge restore runs (queue down → inline) and reports a summary.
    from app.workers import queue as queue_mod

    monkeypatch.setattr(queue_mod, "enqueue_backup_restore", lambda **kw: None)
    resp = client.post(
        f"/api/v1/admin/backups/{name}/restore",
        headers=auth_headers("owner"),
        json={"mode": "merge"},
    )
    assert resp.status_code == 202
    body = resp.json()
    assert body["queued"] is False and body["summary"]["mode"] == "merge"


def test_upload_rejects_non_archive(client, auth_headers, storage_tmp) -> None:
    resp = client.post(
        "/api/v1/admin/backups/upload",
        headers=auth_headers("owner"),
        files={"upload": ("junk.zip", io.BytesIO(b"not a zip"), "application/zip")},
    )
    assert resp.status_code == 400
