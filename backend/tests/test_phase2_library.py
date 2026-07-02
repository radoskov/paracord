"""Phase 2 — references shorthand, file content_available, hash search (items 6, 9, 10)."""

from app.models.citation import CitationMention, Reference
from app.models.file import File, FileWorkLink
from app.models.work import Work


def test_reference_shorthand_from_mention_marker(client, auth_headers, db):
    """A reference's derived ``shorthand`` is the most common linked mention marker_text."""
    citing = Work(canonical_title="Citing", normalized_title="citing")
    db.add(citing)
    db.flush()
    ref = Reference(citing_work_id=citing.id, title="A Cited Paper", year=2020)
    other = Reference(citing_work_id=citing.id, title="No Mentions", year=2019)
    db.add_all([ref, other])
    db.flush()
    # "[69]" appears twice, "[xx]" once → most common wins.
    db.add_all(
        [
            CitationMention(citing_work_id=citing.id, reference_id=ref.id, marker_text="[69]"),
            CitationMention(citing_work_id=citing.id, reference_id=ref.id, marker_text="[69]"),
            CitationMention(citing_work_id=citing.id, reference_id=ref.id, marker_text="[xx]"),
        ]
    )
    db.commit()

    resp = client.get(f"/api/v1/works/{citing.id}/references", headers=auth_headers("reader"))
    assert resp.status_code == 200
    by_title = {r["title"]: r for r in resp.json()}
    assert by_title["A Cited Paper"]["shorthand"] == "[69]"
    # A reference with no in-text mention has no shorthand.
    assert by_title["No Mentions"]["shorthand"] is None


def test_file_content_available_false_for_extracted_discarded(client, auth_headers, db):
    """``content_available`` is False when the PDF was discarded after extract-only."""
    work = Work(canonical_title="Has File", normalized_title="has file")
    db.add(work)
    db.flush()
    file = File(
        sha256="a" * 64,
        size_bytes=1234,
        original_filename="paper.pdf",
        status="extracted_discarded",
    )
    db.add(file)
    db.flush()
    db.add(FileWorkLink(file_id=file.id, work_id=work.id))
    db.commit()

    resp = client.get(f"/api/v1/works/{work.id}/files", headers=auth_headers("reader"))
    assert resp.status_code == 200
    files = resp.json()
    assert len(files) == 1
    assert files[0]["status"] == "extracted_discarded"
    assert files[0]["content_available"] is False


def test_hash_search_returns_owning_paper(client, auth_headers, db):
    """A sha256 (or prefix) typed into the library search matches the paper owning that file."""
    work = Work(canonical_title="Owner Of Hashed File", normalized_title="owner of hashed file")
    db.add(work)
    db.flush()
    sha = "0123456789abcdef" * 4  # 64 hex chars
    file = File(sha256=sha, size_bytes=10, original_filename="x.pdf", status="extracted")
    db.add(file)
    db.flush()
    db.add(FileWorkLink(file_id=file.id, work_id=work.id))
    db.commit()

    h = auth_headers("reader")
    # Full hash.
    full = client.get(f"/api/v1/works?q={sha}", headers=h)
    assert full.status_code == 200
    assert any(w["id"] == str(work.id) for w in full.json()["items"])
    # Hash prefix (as shown truncated in the UI).
    prefix = client.get(f"/api/v1/works?q={sha[:12]}", headers=h)
    assert any(w["id"] == str(work.id) for w in prefix.json()["items"])
