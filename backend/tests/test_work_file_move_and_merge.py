"""Issue 4 (batch 9): move a PDF between papers, and merge one arbitrary paper into another.

Exercises the two new work endpoints:
* ``POST /works/{id}/files/{file_id}/move`` — re-point a FileWorkLink to another paper.
* ``POST /works/{id}/merge`` / ``GET /works/{id}/merge-preview`` — expose the existing
  duplicate-resolution merge for any two papers (not only duplicate-scan candidates).
"""

from app.models.file import File, FileWorkLink
from app.models.work import Work


def _work(db, title: str) -> Work:
    work = Work(canonical_title=title, normalized_title=title.lower())
    db.add(work)
    db.flush()
    return work


def _file(db, sha: str) -> File:
    file = File(sha256=sha, size_bytes=100, original_filename="paper.pdf", status="extracted")
    db.add(file)
    db.flush()
    return file


def test_move_file_repoints_link_to_target(client, auth_headers, db):
    """Moving a PDF detaches it from the source and attaches it to the target; main-file follows."""
    source = _work(db, "Stub")
    target = _work(db, "Full Paper")
    file = _file(db, "a" * 64)
    db.add(FileWorkLink(file_id=file.id, work_id=source.id))
    source.main_file_id = file.id  # the stub's only/main file
    db.commit()

    resp = client.post(
        f"/api/v1/works/{source.id}/files/{file.id}/move",
        json={"target_work_id": str(target.id)},
        headers=auth_headers("owner"),
    )
    assert resp.status_code == 200
    assert resp.json()["id"] == str(file.id)

    # The link now points at the target; the source no longer lists the file.
    db.expire_all()
    assert db.query(FileWorkLink).filter_by(file_id=file.id, work_id=target.id).count() == 1
    assert db.query(FileWorkLink).filter_by(file_id=file.id, work_id=source.id).count() == 0
    # main_file cleared on the source, adopted by the target (which had none).
    assert db.get(Work, source.id).main_file_id is None
    assert db.get(Work, target.id).main_file_id == file.id


def test_files_report_also_in_count_for_shared_pdf(client, auth_headers, db):
    """batch10: a deduped PDF linked to another paper reports also_in_count>0 (duplicate badge)."""
    a = _work(db, "Paper A")
    b = _work(db, "Paper B")
    shared = _file(db, "f" * 64)
    db.add_all(
        [
            FileWorkLink(file_id=shared.id, work_id=a.id),
            FileWorkLink(file_id=shared.id, work_id=b.id),
        ]
    )
    db.commit()

    resp = client.get(f"/api/v1/works/{b.id}/files", headers=auth_headers("owner"))
    assert resp.status_code == 200
    files = resp.json()
    assert len(files) == 1
    assert files[0]["also_in_count"] == 1  # also attached to paper A

    # A paper whose file is unique reports 0.
    solo = _work(db, "Solo")
    only = _file(db, "9" * 64)
    db.add(FileWorkLink(file_id=only.id, work_id=solo.id))
    db.commit()
    solo_files = client.get(f"/api/v1/works/{solo.id}/files", headers=auth_headers("owner")).json()
    assert solo_files[0]["also_in_count"] == 0


def test_move_file_to_same_paper_is_400(client, auth_headers, db):
    work = _work(db, "Solo")
    file = _file(db, "b" * 64)
    db.add(FileWorkLink(file_id=file.id, work_id=work.id))
    db.commit()

    resp = client.post(
        f"/api/v1/works/{work.id}/files/{file.id}/move",
        json={"target_work_id": str(work.id)},
        headers=auth_headers("owner"),
    )
    assert resp.status_code == 400


def test_move_file_not_attached_is_404(client, auth_headers, db):
    source = _work(db, "No File Here")
    target = _work(db, "Target")
    file = _file(db, "c" * 64)  # exists but not linked to source
    db.commit()

    resp = client.post(
        f"/api/v1/works/{source.id}/files/{file.id}/move",
        json={"target_work_id": str(target.id)},
        headers=auth_headers("owner"),
    )
    assert resp.status_code == 404


def test_move_file_missing_target_is_404(client, auth_headers, db):
    import uuid

    source = _work(db, "Has File")
    file = _file(db, "d" * 64)
    db.add(FileWorkLink(file_id=file.id, work_id=source.id))
    db.commit()

    resp = client.post(
        f"/api/v1/works/{source.id}/files/{file.id}/move",
        json={"target_work_id": str(uuid.uuid4())},
        headers=auth_headers("owner"),
    )
    assert resp.status_code == 404


def test_move_file_already_on_target_just_drops_source_link(client, auth_headers, db):
    """If the file is already linked to the target, the move only removes the source link."""
    source = _work(db, "Source")
    target = _work(db, "Target")
    file = _file(db, "e" * 64)
    db.add_all(
        [
            FileWorkLink(file_id=file.id, work_id=source.id),
            FileWorkLink(file_id=file.id, work_id=target.id),
        ]
    )
    db.commit()

    resp = client.post(
        f"/api/v1/works/{source.id}/files/{file.id}/move",
        json={"target_work_id": str(target.id)},
        headers=auth_headers("owner"),
    )
    assert resp.status_code == 200
    db.expire_all()
    assert db.query(FileWorkLink).filter_by(file_id=file.id, work_id=source.id).count() == 0
    assert db.query(FileWorkLink).filter_by(file_id=file.id, work_id=target.id).count() == 1


def test_merge_paper_moves_files_and_hides_source(client, auth_headers, db):
    """Merging a source paper into a base moves its file and hides the source as a shadow."""
    base = _work(db, "Canonical")
    source = _work(db, "Duplicate To Fold In")
    file = _file(db, "f" * 64)
    db.add(FileWorkLink(file_id=file.id, work_id=source.id))
    db.commit()

    resp = client.post(
        f"/api/v1/works/{base.id}/merge",
        json={"source_work_id": str(source.id)},
        headers=auth_headers("owner"),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == str(base.id)
    assert body["has_reversible_shadow"] is True

    db.expire_all()
    # The file link moved onto the base; the source is now a hidden merged shadow.
    assert db.query(FileWorkLink).filter_by(file_id=file.id, work_id=base.id).count() == 1
    assert db.get(Work, source.id).merged_into_id == base.id


def test_merge_preview_reports_moved_file_count(client, auth_headers, db):
    base = _work(db, "Base")
    source = _work(db, "Src")
    file = _file(db, "1" * 64)
    db.add(FileWorkLink(file_id=file.id, work_id=source.id))
    db.commit()

    resp = client.get(
        f"/api/v1/works/{base.id}/merge-preview?source_work_id={source.id}",
        headers=auth_headers("owner"),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["base_work_id"] == str(base.id)
    assert body["source_work_id"] == str(source.id)
    assert body["file_count"] == 1


def test_merge_paper_into_itself_is_400(client, auth_headers, db):
    work = _work(db, "Self")
    db.commit()
    resp = client.post(
        f"/api/v1/works/{work.id}/merge",
        json={"source_work_id": str(work.id)},
        headers=auth_headers("owner"),
    )
    assert resp.status_code == 400
