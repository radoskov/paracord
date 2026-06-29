"""Tests for single-PDF upload and identifier import (P2/item10)."""

import io
import uuid
from unittest.mock import patch

from app.models.file import File, FileWorkLink, Location
from app.models.work import Work
from app.services.metadata_enrichment import ExternalMetadata

_PREVIEW = {"page_count": 1, "preview_text": "Sample text.", "text_layer_quality": "good"}

# Minimal valid PDF bytes (just the header — enough to pass the magic-byte check).
_TINY_PDF = b"%PDF-1.4\n%%EOF\n"


# ---------------------------------------------------------------------------
# PDF upload
# ---------------------------------------------------------------------------


def test_upload_pdf_creates_file_and_work(client, auth_headers, db, tmp_path) -> None:
    from app.core.config import get_settings

    settings = get_settings()
    with (
        patch("app.services.storage.get_settings", return_value=settings),
        patch(
            "app.services.storage._extract_pdf_preview",
            return_value={
                "page_count": 1,
                "preview_text": "Sample text.",
                "text_layer_quality": "good",
            },
        ),
    ):
        r = client.post(
            "/api/v1/imports/upload",
            headers=auth_headers("editor"),
            files={"file": ("test_paper.pdf", io.BytesIO(_TINY_PDF), "application/pdf")},
        )
    assert r.status_code == 201
    body = r.json()
    assert body["input_type"] == "upload"
    assert body["status"] == "complete"

    file = db.query(File).filter(File.original_filename == "test_paper.pdf").first()
    assert file is not None
    location = (
        db.query(Location)
        .filter(Location.file_id == file.id, Location.location_type == "managed_path")
        .first()
    )
    assert location is not None


def test_upload_pdf_deduplicates_by_sha256(client, auth_headers, db, tmp_path) -> None:
    from app.core.config import get_settings

    settings = get_settings()
    with (
        patch("app.services.storage.get_settings", return_value=settings),
        patch(
            "app.services.storage._extract_pdf_preview",
            return_value={"page_count": 1, "preview_text": None, "text_layer_quality": "unknown"},
        ),
    ):
        r1 = client.post(
            "/api/v1/imports/upload",
            headers=auth_headers("editor"),
            files={"file": ("paper.pdf", io.BytesIO(_TINY_PDF), "application/pdf")},
        )
        r2 = client.post(
            "/api/v1/imports/upload",
            headers=auth_headers("editor"),
            files={"file": ("paper.pdf", io.BytesIO(_TINY_PDF), "application/pdf")},
        )
    assert r1.status_code == 201
    assert r2.status_code == 201
    # Only one File row should exist (deduped by SHA-256).
    import hashlib

    sha = hashlib.sha256(_TINY_PDF).hexdigest()
    files = db.query(File).filter(File.sha256 == sha).all()
    assert len(files) == 1


def test_upload_pdf_rejects_non_pdf_magic(client, auth_headers) -> None:
    r = client.post(
        "/api/v1/imports/upload",
        headers=auth_headers("editor"),
        files={"file": ("evil.pdf", io.BytesIO(b"Not a PDF file"), "application/pdf")},
    )
    assert r.status_code == 400


def test_upload_pdf_requires_auth(client) -> None:
    r = client.post(
        "/api/v1/imports/upload",
        files={"file": ("p.pdf", io.BytesIO(_TINY_PDF), "application/pdf")},
    )
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Identifier import
# ---------------------------------------------------------------------------

_MOCK_ARXIV_META = ExternalMetadata(
    source="arxiv",
    title="Attention Is All You Need",
    abstract="We propose the Transformer, a model based solely on attention mechanisms.",
    year=2017,
    authors=["Ashish Vaswani", "Noam Shazeer"],
    doi="10.5555/3295222.3295349",
)


def test_identifier_import_arxiv_creates_work(client, auth_headers, db) -> None:
    with patch(
        "app.api.v1.endpoints.imports.enrich_work",
        return_value={"sources": ["arxiv"], "promoted": ["title"]},
    ):
        r = client.post(
            "/api/v1/imports/identifier",
            headers=auth_headers("editor"),
            json={"identifier_type": "arxiv", "value": "1706.03762"},
        )
    assert r.status_code == 201
    body = r.json()
    assert body["created"] is True
    assert "arxiv" in body["enriched_sources"]
    assert db.get(Work, uuid.UUID(body["work_id"])) is not None


def test_identifier_import_arxiv_idempotent(client, auth_headers, db) -> None:
    work = Work(
        canonical_title="Existing",
        normalized_title="existing",
        canonical_metadata_source="identifier",
        arxiv_id="1706.03762",
        arxiv_base_id="1706.03762",
    )
    db.add(work)
    db.commit()

    with patch(
        "app.api.v1.endpoints.imports.enrich_work",
        return_value={"sources": [], "promoted": []},
    ):
        r = client.post(
            "/api/v1/imports/identifier",
            headers=auth_headers("editor"),
            json={"identifier_type": "arxiv", "value": "1706.03762"},
        )
    assert r.status_code == 201
    body = r.json()
    assert body["created"] is False
    assert str(work.id) == body["work_id"]


def test_identifier_import_doi_creates_work(client, auth_headers, db) -> None:
    with patch(
        "app.api.v1.endpoints.imports.enrich_work",
        return_value={"sources": ["crossref"], "promoted": ["title"]},
    ):
        r = client.post(
            "/api/v1/imports/identifier",
            headers=auth_headers("editor"),
            json={"identifier_type": "doi", "value": "10.1234/test.doi"},
        )
    assert r.status_code == 201
    body = r.json()
    assert body["created"] is True


def test_identifier_import_rejects_empty_value(client, auth_headers) -> None:
    r = client.post(
        "/api/v1/imports/identifier",
        headers=auth_headers("editor"),
        json={"identifier_type": "arxiv", "value": ""},
    )
    assert r.status_code == 400


def test_identifier_import_requires_auth(client) -> None:
    r = client.post(
        "/api/v1/imports/identifier",
        json={"identifier_type": "arxiv", "value": "1706.03762"},
    )
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Attach a PDF to an existing work (6F)
# ---------------------------------------------------------------------------


def test_upload_file_to_work_attaches_and_links(client, auth_headers, db) -> None:
    from app.core.config import get_settings

    headers = auth_headers("editor")
    work = client.post(
        "/api/v1/works", headers=headers, json={"canonical_title": "Manual Work"}
    ).json()

    with (
        patch("app.services.storage.get_settings", return_value=get_settings()),
        patch("app.services.storage._extract_pdf_preview", return_value=_PREVIEW),
    ):
        r = client.post(
            f"/api/v1/works/{work['id']}/files",
            headers=headers,
            files={"file": ("p.pdf", io.BytesIO(_TINY_PDF), "application/pdf")},
        )
    assert r.status_code == 201
    assert r.json()["original_filename"] == "p.pdf"

    listing = client.get(f"/api/v1/works/{work['id']}/files", headers=headers).json()
    assert len(listing) == 1
    link = db.query(FileWorkLink).filter(FileWorkLink.work_id == uuid.UUID(work["id"])).first()
    assert link is not None


def test_upload_file_to_work_rejects_non_pdf(client, auth_headers) -> None:
    headers = auth_headers("editor")
    work = client.post("/api/v1/works", headers=headers, json={"canonical_title": "W"}).json()
    r = client.post(
        f"/api/v1/works/{work['id']}/files",
        headers=headers,
        files={"file": ("note.txt", io.BytesIO(b"not a pdf"), "text/plain")},
    )
    assert r.status_code == 400


def test_list_work_files_empty_and_missing(client, auth_headers) -> None:
    headers = auth_headers("editor")
    work = client.post("/api/v1/works", headers=headers, json={"canonical_title": "W"}).json()
    assert client.get(f"/api/v1/works/{work['id']}/files", headers=headers).json() == []
    missing = client.get(f"/api/v1/works/{uuid.uuid4()}/files", headers=headers)
    assert missing.status_code == 404


# ---------------------------------------------------------------------------
# Delete a paper (Stage 4.5 / 6B)
# ---------------------------------------------------------------------------


def test_delete_work_removes_paper_and_links(client, auth_headers, db) -> None:
    from app.core.config import get_settings

    headers = auth_headers("editor")
    work = client.post(
        "/api/v1/works", headers=headers, json={"canonical_title": "Throwaway"}
    ).json()
    with (
        patch("app.services.storage.get_settings", return_value=get_settings()),
        patch("app.services.storage._extract_pdf_preview", return_value=_PREVIEW),
    ):
        client.post(
            f"/api/v1/works/{work['id']}/files",
            headers=headers,
            files={"file": ("p.pdf", io.BytesIO(_TINY_PDF), "application/pdf")},
        )

    deleted = client.delete(f"/api/v1/works/{work['id']}", headers=headers)
    assert deleted.status_code == 204
    assert client.get(f"/api/v1/works/{work['id']}", headers=headers).status_code == 404
    assert (
        db.query(FileWorkLink).filter(FileWorkLink.work_id == uuid.UUID(work["id"])).first() is None
    )


def test_delete_work_missing_returns_404(client, auth_headers) -> None:
    r = client.delete(f"/api/v1/works/{uuid.uuid4()}", headers=auth_headers("editor"))
    assert r.status_code == 404


def test_delete_work_requires_editor(client, auth_headers) -> None:
    work = client.post(
        "/api/v1/works", headers=auth_headers("editor"), json={"canonical_title": "X"}
    ).json()
    r = client.delete(f"/api/v1/works/{work['id']}", headers=auth_headers("reader"))
    assert r.status_code == 403
