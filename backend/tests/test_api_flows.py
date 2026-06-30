"""High-level, user-oriented API flow tests (HTTP via TestClient).

These exercise the real product loops a user performs, end to end through the API:
import → organize → search → read (M1), and the extraction review surface (M2). They
complement the service-level unit tests by covering routing, auth, schemas, and wiring.
"""

import pytest
from app.models.citation import CitationMention, Reference
from app.models.metadata import MetadataAssertion
from app.models.work import Work


@pytest.fixture()
def no_queue(monkeypatch):
    """Make background enqueue a no-op so flows don't depend on Redis."""
    monkeypatch.setattr("app.api.v1.endpoints.imports.enqueue_extraction", lambda *_: None)
    monkeypatch.setattr("app.api.v1.endpoints.works.enqueue_enrichment", lambda *_: "job-test")


@pytest.fixture()
def server_root(tmp_path, monkeypatch):
    """Configure a server-folder root (alias 'papers') with one arXiv-named PDF."""
    from app.core.config import Settings

    papers = tmp_path / "papers"
    papers.mkdir()
    (papers / "1706.03762.pdf").write_bytes(b"%PDF-1.4\n% test fixture\n")
    settings = Settings(server_allowed_roots=[{"alias": "papers", "path": str(papers)}])
    monkeypatch.setattr("app.api.v1.endpoints.sources.get_settings", lambda: settings)
    return papers


# --- M1: the core library loop ---------------------------------------------


def test_m1_import_organize_search_read(client, auth_headers, server_root, no_queue):
    # The organize step creates shelves/racks (librarian+ in Phase H).
    h = auth_headers("librarian")

    # import a configured server folder
    src = client.post(
        "/api/v1/sources/server-folder", headers=h, json={"name": "Papers", "path_alias": "papers"}
    )
    assert src.status_code == 201
    batch = client.post("/api/v1/imports/folder", headers=h, json={"source_id": src.json()["id"]})
    assert batch.status_code == 201
    assert batch.json()["stats"]["created_works"] == 1

    # the work exists, with the arXiv id detected from the filename
    works = client.get("/api/v1/works", headers=h).json()
    assert len(works) == 1
    work = works[0]
    wid = work["id"]
    assert work["arxiv_id"] == "1706.03762"

    # organize: shelf -> rack, and a tag
    shelf = client.post("/api/v1/shelves", headers=h, json={"name": "Transformers"}).json()
    assert (
        client.post(
            f"/api/v1/shelves/{shelf['id']}/works", headers=h, json={"work_id": wid}
        ).status_code
        == 204
    )
    assert [
        w["id"] for w in client.get(f"/api/v1/shelves/{shelf['id']}/works", headers=h).json()
    ] == [wid]

    rack = client.post("/api/v1/racks", headers=h, json={"name": "Thesis"}).json()
    assert (
        client.post(
            f"/api/v1/racks/{rack['id']}/shelves", headers=h, json={"shelf_id": shelf["id"]}
        ).status_code
        == 204
    )
    assert [
        s["id"] for s in client.get(f"/api/v1/racks/{rack['id']}/shelves", headers=h).json()
    ] == [shelf["id"]]

    tag = client.post("/api/v1/tags", headers=h, json={"name": "method"}).json()
    client.post(
        f"/api/v1/tags/{tag['id']}/links", headers=h, json={"entity_type": "work", "entity_id": wid}
    )

    # search / filter by shelf, rack, tag, reading status
    assert [
        w["id"] for w in client.get(f"/api/v1/works?shelf_id={shelf['id']}", headers=h).json()
    ] == [wid]
    assert [
        w["id"] for w in client.get(f"/api/v1/works?rack_id={rack['id']}", headers=h).json()
    ] == [wid]
    assert [w["id"] for w in client.get(f"/api/v1/works?tag_id={tag['id']}", headers=h).json()] == [
        wid
    ]
    client.patch(f"/api/v1/works/{wid}", headers=h, json={"reading_status": "reading"})
    assert [
        w["id"] for w in client.get("/api/v1/works?reading_status=reading", headers=h).json()
    ] == [wid]

    # read: stream the PDF from the configured source
    files = client.get("/api/v1/files", headers=h).json()
    assert len(files) == 1
    assert client.get(f"/api/v1/files/{files[0]['id']}/stream", headers=h).status_code == 200


def test_m1_reimport_is_idempotent(client, auth_headers, server_root, no_queue):
    h = auth_headers("editor")
    src = client.post(
        "/api/v1/sources/server-folder", headers=h, json={"name": "Papers", "path_alias": "papers"}
    ).json()
    client.post("/api/v1/imports/folder", headers=h, json={"source_id": src["id"]})
    second = client.post("/api/v1/imports/folder", headers=h, json={"source_id": src["id"]})
    assert second.json()["stats"] == {
        "seen": 1,
        "created_files": 0,
        "created_works": 0,
        "existing_files": 1,
    }
    assert len(client.get("/api/v1/works", headers=h).json()) == 1


def test_reader_role_cannot_import_or_create(client, auth_headers, server_root, no_queue):
    """Reader is read-only: import and writes are forbidden, reads allowed."""
    r = auth_headers("reader")
    assert (
        client.post(
            "/api/v1/sources/server-folder", headers=r, json={"name": "P", "path_alias": "papers"}
        ).status_code
        == 403
    )
    assert client.post("/api/v1/shelves", headers=r, json={"name": "x"}).status_code == 403
    assert client.get("/api/v1/works", headers=r).status_code == 200


# --- M2: the metadata review surface ---------------------------------------


def _seed_work_with_conflict(db):
    work = Work(
        canonical_title="GROBID Mis-detected Title",
        normalized_title="grobid mis-detected title",
        canonical_metadata_source="grobid",
        arxiv_id="1706.03762",
    )
    db.add(work)
    db.flush()
    db.add_all(
        [
            MetadataAssertion(
                entity_type="work",
                entity_id=work.id,
                field_name="title",
                value="GROBID Mis-detected Title",
                source="grobid",
                selected_as_canonical=False,
            ),
            MetadataAssertion(
                entity_type="work",
                entity_id=work.id,
                field_name="title",
                value="Attention Is All You Need",
                source="arxiv",
                selected_as_canonical=True,
            ),
        ]
    )
    db.commit()
    return work


def test_m2_metadata_review_and_select(client, auth_headers, db):
    work = _seed_work_with_conflict(db)
    h = auth_headers("editor")

    reviews = client.get(f"/api/v1/works/{work.id}/metadata", headers=h).json()
    title_field = next(f for f in reviews if f["field_name"] == "title")
    assert title_field["has_conflict"] is True
    assert title_field["canonical_value"] == "Attention Is All You Need"

    grobid_assertion = next(a for a in title_field["assertions"] if a["source"] == "grobid")
    selected = client.post(
        f"/api/v1/works/{work.id}/metadata/select",
        headers=h,
        json={"assertion_id": grobid_assertion["id"]},
    )
    assert selected.status_code == 200
    assert selected.json()["canonical_title"] == "GROBID Mis-detected Title"


def test_m2_citation_contexts_surface(client, auth_headers, db):
    work = Work(canonical_title="Paper", normalized_title="paper")
    db.add(work)
    db.flush()
    ref = Reference(citing_work_id=work.id, title="Cited Work", raw_citation="Cited Work, 2020")
    db.add(ref)
    db.flush()
    db.add(
        CitationMention(
            citing_work_id=work.id,
            reference_id=ref.id,
            marker_text="[1]",
            section_label="Introduction",
            context_sentence="As shown in prior work [1], attention helps.",
        )
    )
    db.commit()

    contexts = client.get(
        f"/api/v1/works/{work.id}/citation-contexts", headers=auth_headers("reader")
    )
    assert contexts.status_code == 200
    body = contexts.json()
    assert len(body) == 1
    assert body[0]["marker_text"] == "[1]"


def test_m2_enrich_trigger_requires_identifier(client, auth_headers, db, no_queue):
    h = auth_headers("editor")
    no_id = client.post(
        "/api/v1/works", headers=h, json={"canonical_title": "No identifiers"}
    ).json()
    assert client.post(f"/api/v1/works/{no_id['id']}/enrich", headers=h).status_code == 400

    with_id = client.post(
        "/api/v1/works", headers=h, json={"canonical_title": "Has arXiv", "arxiv_id": "1706.03762"}
    ).json()
    assert client.post(f"/api/v1/works/{with_id['id']}/enrich", headers=h).status_code == 202


_PDF_BYTES = b"%PDF-1.4\n% extract test fixture\n%%EOF\n"


def test_work_extract_no_files(client, auth_headers):
    """Work-level extract on a paper with no attached files reports no_files (not an error)."""
    h = auth_headers("editor")
    work = client.post("/api/v1/works", headers=h, json={"canonical_title": "No files"}).json()
    resp = client.post(f"/api/v1/works/{work['id']}/extract", headers=h)
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "no_files"
    assert body["queued"] == 0


def test_work_extract_queues_each_file(client, auth_headers, monkeypatch):
    """Work-level extract enqueues GROBID extraction for every attached file (#12)."""
    jobs: list[str] = []

    def fake_enqueue(file_id):
        job = f"job-{len(jobs)}"
        jobs.append(str(file_id))
        return job

    monkeypatch.setattr("app.api.v1.endpoints.works.enqueue_extraction", fake_enqueue)
    h = auth_headers("editor")
    work = client.post("/api/v1/works", headers=h, json={"canonical_title": "Has a PDF"}).json()
    upload = client.post(
        f"/api/v1/works/{work['id']}/files",
        headers=h,
        files={"file": ("paper.pdf", _PDF_BYTES, "application/pdf")},
    )
    assert upload.status_code == 201
    jobs.clear()  # ignore the enqueue fired by the upload itself

    resp = client.post(f"/api/v1/works/{work['id']}/extract", headers=h)
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "queued"
    assert body["queued"] == 1
    assert len(body["job_ids"]) == 1
    assert len(jobs) == 1  # one enqueue per attached file


def test_work_extract_missing_work_404(client, auth_headers):
    import uuid as _uuid  # noqa: PLC0415

    h = auth_headers("editor")
    resp = client.post(f"/api/v1/works/{_uuid.uuid4()}/extract", headers=h)
    assert resp.status_code == 404


def test_work_extract_requires_editor(client, auth_headers):
    """Readers cannot trigger extraction (role gate)."""
    editor = auth_headers("editor")
    work = client.post(
        "/api/v1/works", headers=editor, json={"canonical_title": "Role-gated"}
    ).json()
    reader = auth_headers("reader")
    resp = client.post(f"/api/v1/works/{work['id']}/extract", headers=reader)
    assert resp.status_code == 403
