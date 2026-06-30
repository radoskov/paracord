"""Batch citation import HTTP endpoints (Phase J item 5) + import-to-shelf (item 6).

Network is avoided: preview is monkeypatched so the lookup engine never makes a real request.
Commit is deterministic (no external calls) and exercises the contributor floor + shelf gating.
"""

import uuid

from app.models.organization import Shelf, ShelfWork
from app.models.work import Work
from app.services import batch_import


def test_batch_preview_contributor_floor(client, auth_headers, monkeypatch) -> None:
    """Reader is rejected (contributor floor); contributor gets a 200 with drafts and no writes."""

    def fake_preview(lines, *, engine, settings, fetchers=None, grobid=None):
        return batch_import.BatchPreview(
            drafts=[
                batch_import.ParsedDraft(
                    line_index=0,
                    raw_line=lines[0],
                    engine=engine,
                    suggested_title=lines[0],
                    suggested_authors=[],
                    suggested_year=None,
                    suggested_doi=None,
                    suggested_venue=None,
                    suggested_abstract=None,
                    match_status="title_only",
                )
            ],
            degraded=False,
        )

    monkeypatch.setattr(batch_import, "preview_lines", fake_preview)

    reader = client.post(
        "/api/v1/imports/batch/preview",
        headers=auth_headers("reader"),
        json={"text": "Some citation line", "engine": "lookup"},
    )
    assert reader.status_code == 403

    resp = client.post(
        "/api/v1/imports/batch/preview",
        headers=auth_headers("contributor"),
        json={"text": "Some citation line", "engine": "lookup"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["drafts"]) == 1
    assert body["drafts"][0]["match_status"] == "title_only"
    assert body["degraded"] is False


def test_batch_commit_creates_works(client, auth_headers, db) -> None:
    resp = client.post(
        "/api/v1/imports/batch/commit",
        headers=auth_headers("contributor"),
        json={
            "engine": "grobid",
            "enrich": False,
            "drafts": [
                {"title": "A New Paper", "authors": ["X Y"], "doi": "10.1/abc", "include": True},
                {"title": "Excluded", "include": False},
            ],
        },
    )
    assert resp.status_code == 201
    stats = resp.json()["stats"]
    assert stats["created"] == 1
    assert stats["skipped"] == 1
    assert db.query(Work).filter(Work.doi == "10.1/abc").first() is not None


def test_batch_commit_to_shelf_requires_modify_access(client, auth_headers, db) -> None:
    # An open shelf is modifiable only by librarian+; a contributor commit-to-shelf must 403.
    shelf = Shelf(name="batch-shelf", access_level="open")
    db.add(shelf)
    db.commit()
    db.refresh(shelf)

    denied = client.post(
        "/api/v1/imports/batch/commit",
        headers=auth_headers("contributor"),
        json={
            "engine": "grobid",
            "enrich": False,
            "target_shelf_id": str(shelf.id),
            "drafts": [{"title": "Shelved", "doi": "10.1/sh", "include": True}],
        },
    )
    assert denied.status_code == 403
    # Nothing was committed (rolled back).
    assert db.query(Work).filter(Work.doi == "10.1/sh").first() is None

    allowed = client.post(
        "/api/v1/imports/batch/commit",
        headers=auth_headers("librarian"),
        json={
            "engine": "grobid",
            "enrich": False,
            "target_shelf_id": str(shelf.id),
            "drafts": [{"title": "Shelved", "doi": "10.1/sh", "include": True}],
        },
    )
    assert allowed.status_code == 201
    assert allowed.json()["stats"]["added_to_shelf"] == 1
    work = db.query(Work).filter(Work.doi == "10.1/sh").first()
    assert work is not None
    assert db.get(ShelfWork, {"shelf_id": shelf.id, "work_id": work.id}) is not None


def test_batch_commit_missing_shelf_404(client, auth_headers, db) -> None:
    resp = client.post(
        "/api/v1/imports/batch/commit",
        headers=auth_headers("librarian"),
        json={
            "engine": "grobid",
            "enrich": False,
            "target_shelf_id": str(uuid.uuid4()),
            "drafts": [{"title": "Orphan", "doi": "10.1/orphan", "include": True}],
        },
    )
    assert resp.status_code == 404
