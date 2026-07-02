"""Tests for GET /api/v1/files/{id}/text (reader text / OCR fallback endpoint).

The endpoint is SEE-gated like /stream and returns {text, source}: the native PDF text layer, or
on-the-fly OCR when the native layer is sparse (scanned PDFs).
"""

import hashlib

import pytest
from app.core.config import get_settings
from app.models.file import File, FileWorkLink, Location
from app.models.work import Work
from app.services.storage import content_addressed_path


def _text_pdf_bytes(text: str) -> bytes:
    import fitz  # type: ignore[import-not-found]

    doc = fitz.open()
    page = doc.new_page()
    # Multiple lines so the native text layer is comfortably above the sparse threshold (a single
    # long insert_text line runs off the page and yields too few extractable chars).
    for i in range(12):
        page.insert_text((72, 100 + i * 20), text, fontsize=11)
    data = doc.tobytes()
    doc.close()
    return data


def _image_only_pdf_bytes(word: str) -> bytes:
    """A PDF with NO text layer (rasterised text) so /text must OCR to read it."""
    import fitz  # type: ignore[import-not-found]

    src = fitz.open()
    page = src.new_page()
    page.insert_text((72, 200), word, fontsize=72)
    pix = page.get_pixmap(dpi=150)
    src.close()
    out = fitz.open()
    img_page = out.new_page(width=pix.width, height=pix.height)
    img_page.insert_image(img_page.rect, pixmap=pix)
    data = out.tobytes()
    out.close()
    return data


def _seed_managed_pdf(db, tmp_path, monkeypatch, pdf_bytes: bytes):
    managed_root = tmp_path / "library"
    sha = hashlib.sha256(pdf_bytes).hexdigest()
    dest = content_addressed_path(managed_root, sha)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(pdf_bytes)

    work = Work(canonical_title="t", normalized_title="t")
    file = File(
        sha256=sha,
        size_bytes=len(pdf_bytes),
        mime_type="application/pdf",
        original_filename="x.pdf",
        text_layer_quality="good",
        status="available",
    )
    db.add_all([work, file])
    db.flush()
    db.add_all(
        [
            FileWorkLink(file_id=file.id, work_id=work.id),
            Location(
                file_id=file.id,
                source_id=None,
                location_type="managed_path",
                internal_uri=str(dest),
                is_available=True,
                is_primary=True,
            ),
        ]
    )
    db.commit()

    settings = get_settings().model_copy(update={"managed_library_root": str(managed_root)})
    monkeypatch.setattr("app.api.v1.endpoints.files.get_settings", lambda: settings)
    return file


def test_file_text_returns_native_layer(client, auth_headers, db, tmp_path, monkeypatch):
    file = _seed_managed_pdf(
        db, tmp_path, monkeypatch, _text_pdf_bytes("Hello searchable native world. " * 20)
    )
    r = client.get(f"/api/v1/files/{file.id}/text", headers=auth_headers("reader"))
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "native"
    assert "searchable" in body["text"].lower()


def test_file_text_ocr_fallback_for_scanned_pdf(client, auth_headers, db, tmp_path, monkeypatch):
    from app.services import ocr as ocr_service

    if not ocr_service.pymupdf_available():
        pytest.skip("PyMuPDF / tesseract not available")
    file = _seed_managed_pdf(db, tmp_path, monkeypatch, _image_only_pdf_bytes("OCRWORD"))
    r = client.get(f"/api/v1/files/{file.id}/text", headers=auth_headers("reader"))
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "ocr"  # native layer was empty → OCR fallback
    assert "OCRWORD" in body["text"].upper()


def test_file_text_missing_file_is_404(client, auth_headers):
    import uuid

    r = client.get(f"/api/v1/files/{uuid.uuid4()}/text", headers=auth_headers("reader"))
    assert r.status_code == 404


def test_stream_prefers_derived_ocr_pdf(client, auth_headers, db, tmp_path, monkeypatch):
    """When a derived searchable-OCR copy exists, /stream serves THAT, not the original bytes."""
    from app.services.file_paths import derived_ocr_path

    original = b"%PDF-1.4\n% ORIGINAL scanned bytes\n%%EOF\n"
    file = _seed_managed_pdf(db, tmp_path, monkeypatch, original)
    # The endpoint reads settings via the monkeypatched get_settings; mirror it here to locate the
    # derived path under the same managed root.
    from app.api.v1.endpoints.files import get_settings as _gs

    settings = _gs()
    derived = derived_ocr_path(settings, file.sha256)
    derived.parent.mkdir(parents=True, exist_ok=True)
    derived_bytes = b"%PDF-1.4\n% DERIVED searchable ocr copy\n%%EOF\n"
    derived.write_bytes(derived_bytes)

    r = client.get(f"/api/v1/files/{file.id}/stream", headers=auth_headers("reader"))
    assert r.status_code == 200
    assert r.content == derived_bytes  # served the derived copy, not the original


def test_file_text_is_see_gated(client, auth_headers, db, tmp_path, monkeypatch):
    file = _seed_managed_pdf(db, tmp_path, monkeypatch, _text_pdf_bytes("secret content " * 20))
    # SEE-gating mirrors /stream: a user who can't see the file gets 404, not the text.
    monkeypatch.setattr("app.services.access.can_see_file", lambda *_a, **_k: False)
    r = client.get(f"/api/v1/files/{file.id}/text", headers=auth_headers("reader"))
    assert r.status_code == 404
