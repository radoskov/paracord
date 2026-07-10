"""Multi-PDF staging import (batch 10, issue 1): extract-before-store preview + commit.

Covers the service (stage → extract → collisions → auto-decisions → commit) and the HTTP flow
(upload-multi preview then commit; direct-mode auto-commit; direct-mode DOI collision skipped).
Extraction runs inline (no Redis in tests); GROBID is stubbed to return a fixture TEI.
"""

import uuid
from pathlib import Path

import fitz
import pytest
from app.core.config import get_settings
from app.models.file import File, FileWorkLink
from app.models.import_staging import ImportStagingItem
from app.models.work import Work
from app.services import import_staging
from app.services.grobid_client import GrobidClient
from sqlalchemy import func, select

TEI = (Path(__file__).parent / "fixtures" / "minimal_grobid_tei.xml").read_text(encoding="utf-8")
# Same TEI without the DOI, so two staged papers can be committed together without a DOI clash.
TEI_NO_DOI = TEI.replace('<idno type="DOI">10.5555/transformer</idno>', "")


def _pdf(text: str = "hello") -> bytes:
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


@pytest.fixture()
def managed_root(tmp_path, monkeypatch):
    """Point the managed library at a throwaway dir so staged PDFs are written under tmp_path."""
    monkeypatch.setattr(get_settings(), "managed_library_root", str(tmp_path / "managed"))
    return get_settings()


def _stub_grobid(monkeypatch, tei: str) -> None:
    monkeypatch.setattr(
        GrobidClient,
        "process_fulltext_document_sync",
        lambda self, path: tei,  # noqa: ARG005
    )
    # Force the inline extraction path (as if Redis were absent) so the flow completes in-request
    # without a live worker — mirrors the app's own ``_reindex_runs_inline`` test convention.
    monkeypatch.setattr(
        "app.api.v1.endpoints.imports.enqueue_staging_extraction", lambda item_id: None
    )


# --------------------------------------------------------------------------- service


def test_stage_extract_commit_creates_paper(db, make_user, managed_root):
    actor = make_user("stager", role="editor")
    batch = import_staging.stage_pdfs(
        db, actor=actor, uploads=[("paper.pdf", _pdf())], mode="preview"
    )
    db.commit()
    item = db.scalars(select(ImportStagingItem).where(ImportStagingItem.batch_id == batch.id)).one()
    assert item.status == "pending" and item.file_id is not None

    import_staging.extract_staging_item(db, item=item, fetch_tei=lambda _p: TEI)
    assert item.status == "extracted"
    assert item.parsed["title"] == "Attention Is All You Need"
    assert item.parsed["doi"] == "10.5555/transformer"
    assert item.tei_xml

    assert import_staging.finalize_if_ready(db, batch) is True
    assert batch.status == "ready"

    summary = import_staging.commit_staging(
        db, actor=actor, batch=batch, decisions=[{"item_id": str(item.id), "action": "accept"}]
    )
    db.commit()
    assert summary["created"] == 1
    work = db.get(Work, uuid.UUID(summary["created_work_ids"][0]))
    assert work.canonical_title == "Attention Is All You Need"
    assert db.scalar(select(FileWorkLink).where(FileWorkLink.work_id == work.id))


def test_detect_collisions_same_pdf_and_doi(db, managed_root):
    existing = Work(canonical_title="Existing", normalized_title="existing", doi="10.5555/dup")
    db.add(existing)
    db.flush()
    file = File(sha256="d" * 64, size_bytes=10, status="extracted")
    db.add(file)
    db.flush()
    db.add(FileWorkLink(file_id=file.id, work_id=existing.id))
    db.commit()

    result = import_staging.detect_collisions(db, sha256="d" * 64, doi="10.5555/dup", title=None)
    assert [r["work_id"] for r in result["same_pdf"]] == [str(existing.id)]
    assert [r["work_id"] for r in result["same_doi"]] == [str(existing.id)]


def test_auto_decisions_skips_blocked_and_failed():
    good = ImportStagingItem(filename="a.pdf", status="extracted", duplicates={})
    blocked = ImportStagingItem(
        filename="b.pdf", status="extracted", duplicates={"same_doi": [{"work_id": "x"}]}
    )
    failed = ImportStagingItem(filename="c.pdf", status="extract_failed", error="bad")
    for it in (good, blocked, failed):
        it.id = uuid.uuid4()
    decisions = {d["item_id"]: d for d in import_staging.auto_decisions([good, blocked, failed])}
    assert decisions[str(good.id)]["action"] == "accept"
    assert decisions[str(blocked.id)]["action"] == "skip"
    assert "same_doi" in decisions[str(blocked.id)]["reason"]
    assert decisions[str(failed.id)]["action"] == "skip"


# --------------------------------------------------------------------------- HTTP


def _multi(files):
    return [("files", (name, data, "application/pdf")) for name, data in files]


def test_upload_multi_preview_then_commit(client, auth_headers, managed_root, monkeypatch):
    _stub_grobid(monkeypatch, TEI_NO_DOI)  # no DOI → both papers can be committed together
    headers = auth_headers("editor")
    resp = client.post(
        "/api/v1/imports/upload-multi",
        files=_multi([("a.pdf", _pdf("aaa")), ("b.pdf", _pdf("bbb"))]),
        data={"mode": "preview"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "ready"
    assert len(body["items"]) == 2
    assert {i["status"] for i in body["items"]} == {"extracted"}
    batch_id = body["id"]

    decisions = [{"item_id": i["id"], "action": "accept"} for i in body["items"]]
    commit = client.post(
        f"/api/v1/imports/staging/{batch_id}/commit",
        json={"decisions": decisions},
        headers=headers,
    )
    assert commit.status_code == 200, commit.text
    assert commit.json()["created"] == 2

    got = client.get(f"/api/v1/imports/staging/{batch_id}", headers=headers).json()
    assert got["status"] == "committed"
    assert all(i["created_work_id"] for i in got["items"])


def test_upload_multi_direct_auto_commits(client, auth_headers, managed_root, monkeypatch):
    _stub_grobid(monkeypatch, TEI)
    headers = auth_headers("editor")
    resp = client.post(
        "/api/v1/imports/upload-multi",
        files=_multi([("a.pdf", _pdf("aaa"))]),
        data={"mode": "direct"},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["status"] == "committed"
    assert body["items"][0]["created_work_id"]


def test_upload_multi_direct_skips_duplicate_doi(
    client, auth_headers, db, managed_root, monkeypatch
):
    _stub_grobid(monkeypatch, TEI)  # every extraction yields DOI 10.5555/transformer
    headers = auth_headers("editor")
    first = client.post(
        "/api/v1/imports/upload-multi",
        files=_multi([("a.pdf", _pdf("aaa"))]),
        data={"mode": "direct"},
        headers=headers,
    ).json()
    assert first["items"][0]["created_work_id"]

    # A *different* PDF that extracts to the SAME DOI is blocked (same_doi) in direct mode.
    second = client.post(
        "/api/v1/imports/upload-multi",
        files=_multi([("c.pdf", _pdf("ccc different bytes"))]),
        data={"mode": "direct"},
        headers=headers,
    ).json()
    item = second["items"][0]
    assert item["status"] == "skipped"
    assert "same_doi" in (item["duplicates"] or {})
    assert db.scalar(select(func.count()).select_from(Work)) == 1
