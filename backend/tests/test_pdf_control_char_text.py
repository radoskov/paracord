"""PDFs whose text layer extracts control characters (incl. NUL) must import cleanly.

Old PDFs with custom font encodings (e.g. 1990s LaTeX output) extract glyphs as raw control
codes — including ``\\x00``, which PostgreSQL TEXT columns reject with ``DataError: text fields
cannot contain NUL (0x00) bytes``. That surfaced as a 500 ("NetworkError" in the browser) on
every import path for such files. The fix sanitizes extracted preview text at the source
(``sanitize_extracted_text`` in the storage service, shared with the chunking sanitizer).

SQLite (the unit-test DB) ACCEPTS NUL bytes, so these tests assert on the *stored value*
rather than on the insert succeeding.
"""

from __future__ import annotations

import fitz
from app.models.file import File
from app.services.storage import sanitize_extracted_text
from sqlalchemy import select


def _nul_text_pdf() -> bytes:
    """A real PDF whose first-page text layer round-trips control characters (verified: PyMuPDF
    extracts the literal ``\\x00``/``\\x02`` back out of ``insert_text``)."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Towards\x00a Metho\x02dology for Building Ontologies")
    data = doc.tobytes()
    doc.close()
    return data


def test_sanitize_extracted_text_strips_postgres_hostile_controls():
    assert sanitize_extracted_text("a\x00b\x02c\x1fd") == "a b c d"
    # Whitespace controls the text layer legitimately uses stay.
    assert sanitize_extracted_text("line1\nline2\ttab\rcr") == "line1\nline2\ttab\rcr"
    assert sanitize_extracted_text("") is None
    assert sanitize_extracted_text(None) is None


def test_upload_import_with_nul_text_layer(client, auth_headers, db):
    """The Import-tab upload path stores a NUL-free preview instead of 500ing."""
    resp = client.post(
        "/api/v1/imports/upload",
        headers=auth_headers("editor"),
        files={"file": ("95-ont.pdf", _nul_text_pdf(), "application/pdf")},
    )
    assert resp.status_code in (200, 201), resp.text

    file = db.scalar(select(File).where(File.original_filename == "95-ont.pdf"))
    assert file is not None
    assert file.preview_text is not None
    assert "\x00" not in file.preview_text
    assert "\x02" not in file.preview_text
    # The words survived — sanitizing replaces the control codes, it doesn't drop the text.
    assert "Ontologies" in file.preview_text


def test_attach_file_with_nul_text_layer_to_work(client, auth_headers, db):
    """The paper-detail attach path takes the same sanitized route."""
    h = auth_headers("editor")
    work = client.post(
        "/api/v1/works", headers=h, json={"canonical_title": "NUL text layer"}
    ).json()
    resp = client.post(
        f"/api/v1/works/{work['id']}/files",
        headers=h,
        files={"file": ("nul-attach.pdf", _nul_text_pdf(), "application/pdf")},
    )
    assert resp.status_code == 201, resp.text

    file = db.scalar(select(File).where(File.original_filename == "nul-attach.pdf"))
    assert file is not None and file.preview_text is not None
    assert "\x00" not in file.preview_text
