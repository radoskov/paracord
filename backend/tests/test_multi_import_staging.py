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


def test_staging_preview_runs_the_shared_ocr_prestep(db, make_user, managed_root, monkeypatch):
    """A scanned/textless PDF must get the SAME OCR pre-step in staging preview as in the full
    extraction — regression for scanned imports that previously fed the raw scan to GROBID."""
    from app.services import extraction

    actor = make_user("stager", role="editor")
    batch = import_staging.stage_pdfs(
        db, actor=actor, uploads=[("scan.pdf", _pdf())], mode="preview"
    )
    db.commit()
    item = db.scalars(select(ImportStagingItem).where(ImportStagingItem.batch_id == batch.id)).one()

    calls: list[str] = []

    def fake_ocr(db, *, file, fetch_tei, settings=None, force_ocr=False):
        calls.append(file.sha256)
        return TEI, None  # pretend OCR ran and GROBID returned the fixture TEI

    monkeypatch.setattr(extraction, "ocr_and_fetch_tei", fake_ocr)
    import_staging.extract_staging_item(db, item=item, fetch_tei=lambda _p: "<unused/>")
    assert calls, "staging preview should route TEI fetch through the shared OCR pre-step"
    assert item.status == "extracted"
    assert item.parsed["title"] == "Attention Is All You Need"


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


# --- S-batch item 2: sequential/partial commits + stalled-item self-healing ------------------------


def test_partial_commit_keeps_batch_open_for_sequential_imports(db, make_user, managed_root):
    """Extracted items can be imported while siblings are still processing, repeatedly."""
    actor = make_user("sequencer", role="editor")
    batch = import_staging.stage_pdfs(
        db,
        actor=actor,
        uploads=[("a.pdf", _pdf("a")), ("b.pdf", _pdf("b"))],
        mode="preview",
    )
    db.commit()
    items = list(
        db.scalars(select(ImportStagingItem).where(ImportStagingItem.batch_id == batch.id)).all()
    )
    first, second = items
    import_staging.extract_staging_item(db, item=first, fetch_tei=lambda _p: TEI)
    assert first.status == "extracted" and second.status == "pending"

    # Import the ready one while the other is still pending → batch stays open.
    summary = import_staging.commit_staging(
        db, actor=actor, batch=batch, decisions=[{"item_id": str(first.id), "action": "accept"}]
    )
    db.commit()
    assert summary["created"] == 1
    assert first.status == "committed"
    assert batch.status != "committed"  # still open — second item undecided

    # Second item finishes; a second commit closes the batch.
    import_staging.extract_staging_item(db, item=second, fetch_tei=lambda _p: TEI_NO_DOI)
    summary = import_staging.commit_staging(
        db, actor=actor, batch=batch, decisions=[{"item_id": str(second.id), "action": "accept"}]
    )
    db.commit()
    assert summary["created"] == 1
    assert batch.status == "committed"


def test_requeue_stalled_items_reenqueues_dead_jobs(db, make_user, managed_root, monkeypatch):
    """Items parked pending/extracting with no live job get kicked on each poll."""
    from app.workers import queue as queue_mod

    actor = make_user("healer", role="editor")
    batch = import_staging.stage_pdfs(
        db, actor=actor, uploads=[("a.pdf", _pdf("a"))], mode="preview"
    )
    db.commit()
    item = db.scalars(select(ImportStagingItem).where(ImportStagingItem.batch_id == batch.id)).one()
    item.status = "extracting"  # simulate a worker that died mid-job
    db.commit()

    kicked_ids = []
    monkeypatch.setattr(
        queue_mod,
        "enqueue_staging_extraction",
        lambda item_id: kicked_ids.append(str(item_id)) or f"stage-extract-{item_id}",
    )
    assert import_staging.requeue_stalled_items(db, batch) == 1
    assert kicked_ids == [str(item.id)]


def test_requeue_falls_back_to_inline_extraction_without_queue(
    db, make_user, managed_root, monkeypatch
):
    from app.workers import queue as queue_mod

    actor = make_user("offline", role="editor")
    batch = import_staging.stage_pdfs(
        db, actor=actor, uploads=[("a.pdf", _pdf("a"))], mode="preview"
    )
    db.commit()
    item = db.scalars(select(ImportStagingItem).where(ImportStagingItem.batch_id == batch.id)).one()

    monkeypatch.setattr(queue_mod, "enqueue_staging_extraction", lambda _i: None)
    monkeypatch.setattr(
        GrobidClient, "process_fulltext_document_sync", lambda self, _p: TEI, raising=True
    )
    assert import_staging.requeue_stalled_items(db, batch) == 1
    db.flush()
    assert item.status == "extracted"


def test_auto_commit_rejected_while_extracting(client, auth_headers, db, make_user, managed_root):
    actor = make_user("autoer", role="editor")
    batch = import_staging.stage_pdfs(
        db, actor=actor, uploads=[("a.pdf", _pdf("a"))], mode="preview"
    )
    db.commit()
    resp = client.post(
        f"/api/v1/imports/staging/{batch.id}/commit",
        headers=auth_headers("owner"),
        json={"auto": True},
    )
    assert resp.status_code == 409
    assert "still extracting" in resp.json()["detail"]


# --------------------------------------------------------------- collision append + DOI editing


def _staged_extracted_item(db, actor, tei=TEI, filename="paper.pdf", text="hello"):
    """Stage one PDF and run its preview extraction; returns (batch, item)."""
    batch = import_staging.stage_pdfs(
        db, actor=actor, uploads=[(filename, _pdf(text))], mode="preview"
    )
    db.commit()
    item = db.scalars(select(ImportStagingItem).where(ImportStagingItem.batch_id == batch.id)).one()
    import_staging.extract_staging_item(db, item=item, fetch_tei=lambda _p: tei)
    import_staging.finalize_if_ready(db, batch)
    db.commit()
    return batch, item


def test_append_attaches_pdf_and_applies_extraction_to_unextracted_work(
    db, make_user, managed_root
):
    """Append to a PDF-less paper: file linked, stored TEI applied, no new Work minted."""
    actor = make_user("appender", role="editor")
    existing = Work(
        canonical_title="Attention Is All You Need",
        normalized_title="attention is all you need",
        doi="10.5555/transformer",
    )
    db.add(existing)
    db.commit()
    batch, item = _staged_extracted_item(db, actor)
    assert item.duplicates.get("same_doi")  # the collision the user resolves via append

    summary = import_staging.commit_staging(
        db,
        actor=actor,
        batch=batch,
        decisions=[
            {"item_id": str(item.id), "action": "append", "target_work_id": str(existing.id)}
        ],
    )
    db.commit()
    assert summary["appended"] == 1 and summary["created"] == 0
    assert summary["appended_work_ids"] == [str(existing.id)]
    # No second Work with this DOI; the file is linked to the existing paper.
    assert db.scalar(select(func.count(Work.id)).where(Work.doi == "10.5555/transformer")) == 1
    link = db.scalar(select(FileWorkLink).where(FileWorkLink.work_id == existing.id))
    assert link is not None and link.file_id == item.file_id
    # The stored preview TEI was applied (extraction landed without a fresh GROBID run).
    from app.models.citation import RawTeiDocument

    assert db.scalar(select(RawTeiDocument.id).where(RawTeiDocument.work_id == existing.id))
    assert item.status == "committed" and item.created_work_id == existing.id


def test_append_keeps_existing_extraction_intact(db, make_user, managed_root):
    """Appending to an already-extracted paper attaches the file but leaves its extraction alone."""
    from app.models.citation import RawTeiDocument

    actor = make_user("appender2", role="editor")
    existing = Work(canonical_title="Extracted already", normalized_title="extracted already")
    prior_file = File(sha256="e" * 64, size_bytes=10, status="extracted")
    db.add_all([existing, prior_file])
    db.flush()
    db.add(
        RawTeiDocument(
            work_id=existing.id, file_id=prior_file.id, source="grobid", tei_xml="<TEI/>"
        )
    )
    db.commit()

    batch, item = _staged_extracted_item(db, actor, filename="alt.pdf", text="alt bytes")
    summary = import_staging.commit_staging(
        db,
        actor=actor,
        batch=batch,
        decisions=[
            {"item_id": str(item.id), "action": "append", "target_work_id": str(existing.id)}
        ],
    )
    db.commit()
    assert summary["appended"] == 1
    assert db.scalar(select(FileWorkLink).where(FileWorkLink.work_id == existing.id)) is not None
    # Still exactly the one pre-existing TEI — the second PDF did not overwrite the extraction.
    assert (
        db.scalar(
            select(func.count(RawTeiDocument.id)).where(RawTeiDocument.work_id == existing.id)
        )
        == 1
    )


def test_append_respects_modify_acl(db, make_user, managed_root):
    """A contributor cannot append to somebody else's paper — warned, nothing attached."""
    owner = make_user("owner-of-paper", role="editor")
    actor = make_user("mere-contributor", role="contributor")
    theirs = Work(
        canonical_title="Not yours",
        normalized_title="not yours",
        created_by_user_id=owner.id,
    )
    db.add(theirs)
    db.commit()

    batch, item = _staged_extracted_item(db, actor)
    summary = import_staging.commit_staging(
        db,
        actor=actor,
        batch=batch,
        decisions=[{"item_id": str(item.id), "action": "append", "target_work_id": str(theirs.id)}],
    )
    db.commit()
    assert summary["appended"] == 0
    assert any("permission" in w for w in summary["warnings"])
    assert db.scalar(select(FileWorkLink).where(FileWorkLink.work_id == theirs.id)) is None


def test_same_doi_sibling_gets_precise_warning(db, make_user, managed_root):
    """Two files in one batch with the same DOI (book vs. chapter): the second names the first."""
    actor = make_user("sibling", role="editor")
    batch = import_staging.stage_pdfs(
        db,
        actor=actor,
        uploads=[("book.pdf", _pdf("book")), ("chapter.pdf", _pdf("chapter"))],
        mode="preview",
    )
    db.commit()
    items = list(
        db.scalars(select(ImportStagingItem).where(ImportStagingItem.batch_id == batch.id))
    )
    for item in items:
        import_staging.extract_staging_item(db, item=item, fetch_tei=lambda _p: TEI)
    import_staging.finalize_if_ready(db, batch)

    summary = import_staging.commit_staging(
        db,
        actor=actor,
        batch=batch,
        decisions=[{"item_id": str(i.id), "action": "accept"} for i in items],
    )
    db.commit()
    assert summary["created"] == 1
    assert len(summary["warnings"]) == 1
    warning = summary["warnings"][0]
    assert "same DOI as" in warning and "in this batch" in warning
    assert "edit/clear one DOI" in warning


def test_accept_against_library_doi_owner_suggests_append(db, make_user, managed_root):
    """Creating over an existing paper's DOI is refused with the actionable append hint."""
    actor = make_user("hinted", role="editor")
    existing = Work(
        canonical_title="The Transformer Paper",
        normalized_title="the transformer paper",
        doi="10.5555/transformer",
    )
    db.add(existing)
    db.commit()
    batch, item = _staged_extracted_item(db, actor)

    summary = import_staging.commit_staging(
        db, actor=actor, batch=batch, decisions=[{"item_id": str(item.id), "action": "accept"}]
    )
    db.commit()
    assert summary["created"] == 0
    assert any(
        "Attach PDF to it" in w and "The Transformer Paper" in w for w in summary["warnings"]
    )


def test_clearing_doi_in_preview_unblocks_create(db, make_user, managed_root):
    """The book-vs-chapter fix: clear one item's DOI in preview, then create it normally."""
    actor = make_user("doi-editor", role="editor")
    existing = Work(
        canonical_title="Owns the DOI", normalized_title="owns the doi", doi="10.5555/transformer"
    )
    db.add(existing)
    db.commit()
    batch, item = _staged_extracted_item(db, actor)
    assert item.duplicates.get("same_doi")

    import_staging.set_item_doi(db, item=item, doi=None)
    db.commit()
    assert not (item.duplicates or {}).get("same_doi")
    assert item.parsed["doi"] is None

    summary = import_staging.commit_staging(
        db, actor=actor, batch=batch, decisions=[{"item_id": str(item.id), "action": "accept"}]
    )
    db.commit()
    assert summary["created"] == 1
    minted = db.get(Work, uuid.UUID(summary["created_work_ids"][0]))
    # The cleared DOI stays cleared — GROBID's original in the stored TEI does not resurrect it.
    assert minted.doi is None


def test_append_does_not_steal_a_claimed_doi(db, make_user, managed_root):
    """Applying TEI to a DOI-less paper never promotes a DOI another paper owns (no 500)."""
    actor = make_user("no-steal", role="editor")
    owner = Work(
        canonical_title="DOI owner", normalized_title="doi owner", doi="10.5555/transformer"
    )
    target = Work(canonical_title="Same title match", normalized_title="same title match")
    db.add_all([owner, target])
    db.commit()

    batch, item = _staged_extracted_item(db, actor)
    summary = import_staging.commit_staging(
        db,
        actor=actor,
        batch=batch,
        decisions=[{"item_id": str(item.id), "action": "append", "target_work_id": str(target.id)}],
    )
    db.commit()
    assert summary["appended"] == 1, summary["warnings"]
    db.refresh(target)
    assert target.doi is None  # recorded as a reviewable assertion, not stolen
    db.refresh(owner)
    assert owner.doi == "10.5555/transformer"


def test_patch_staging_item_doi_endpoint(client, auth_headers, managed_root, monkeypatch):
    """HTTP flow: edit a staged item's DOI in preview; collisions re-detected in the response."""
    _stub_grobid(monkeypatch, TEI)
    h = auth_headers("editor")
    existing = client.post(
        "/api/v1/works",
        headers=h,
        json={"canonical_title": "Existing DOI owner", "doi": "10.5555/transformer"},
    )
    assert existing.status_code == 201, existing.text

    batch = client.post(
        "/api/v1/imports/upload-multi",
        headers=h,
        files=[("files", ("one.pdf", _pdf("one"), "application/pdf"))],
        data={"mode": "preview"},
    ).json()
    item = batch["items"][0]
    assert item["duplicates"].get("same_doi")

    patched = client.patch(
        f"/api/v1/imports/staging/{batch['id']}/items/{item['id']}",
        headers=h,
        json={"doi": None},
    )
    assert patched.status_code == 200, patched.text
    body = patched.json()
    assert body["parsed"]["doi"] is None
    assert not (body["duplicates"] or {}).get("same_doi")


def test_commit_append_endpoint_roundtrip(client, auth_headers, db, managed_root, monkeypatch):
    """HTTP flow: append decision through the commit endpoint attaches to the existing paper."""
    _stub_grobid(monkeypatch, TEI)
    h = auth_headers("editor")
    existing = client.post(
        "/api/v1/works",
        headers=h,
        json={"canonical_title": "Append target", "doi": "10.5555/transformer"},
    ).json()

    batch = client.post(
        "/api/v1/imports/upload-multi",
        headers=h,
        files=[("files", ("two.pdf", _pdf("two"), "application/pdf"))],
        data={"mode": "preview"},
    ).json()
    item = batch["items"][0]

    result = client.post(
        f"/api/v1/imports/staging/{batch['id']}/commit",
        headers=h,
        json={
            "decisions": [
                {"item_id": item["id"], "action": "append", "target_work_id": existing["id"]}
            ]
        },
    )
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["appended"] == 1 and body["created"] == 0
    files = client.get(f"/api/v1/works/{existing['id']}/files", headers=h).json()
    assert len(files) == 1


def test_per_item_shelf_overrides_batch_shelf(db, make_user, managed_root):
    """An accepted item with its own target_shelf_id lands there; others use the batch shelf."""
    from app.models.organization import Shelf, ShelfWork

    actor = make_user("shelver", role="librarian")  # shelf membership needs librarian+
    global_shelf = Shelf(name="Global", access_level="open")
    custom_shelf = Shelf(name="Custom", access_level="open")
    db.add_all([global_shelf, custom_shelf])
    db.commit()

    batch = import_staging.stage_pdfs(
        db,
        actor=actor,
        uploads=[("one.pdf", _pdf("one")), ("two.pdf", _pdf("two"))],
        mode="preview",
        target_shelf_id=global_shelf.id,
    )
    db.commit()
    items = list(
        db.scalars(select(ImportStagingItem).where(ImportStagingItem.batch_id == batch.id))
    )
    for item in items:
        import_staging.extract_staging_item(db, item=item, fetch_tei=lambda _p: TEI_NO_DOI)
    import_staging.finalize_if_ready(db, batch)

    summary = import_staging.commit_staging(
        db,
        actor=actor,
        batch=batch,
        decisions=[
            {"item_id": str(items[0].id), "action": "accept"},
            {
                "item_id": str(items[1].id),
                "action": "accept",
                "target_shelf_id": str(custom_shelf.id),
            },
        ],
    )
    db.commit()
    assert summary["created"] == 2, [i.error for i in items]
    shelf_of = {sw.work_id: sw.shelf_id for sw in db.scalars(select(ShelfWork)).all()}
    assert shelf_of[items[0].created_work_id] == global_shelf.id
    assert shelf_of[items[1].created_work_id] == custom_shelf.id
