"""Unit tests for the parser-level PDF upload probe (AUDIT E2, ``storage.probe_pdf_openable``).

The probe fails *closed*: a well-formed ``%PDF`` header wrapping bytes GROBID/OCR cannot process
(encrypted, structurally broken, page-less) must be rejected at upload time, not deep in a worker.
"""

from __future__ import annotations

import fitz
from app.services.storage import probe_pdf_openable


def _valid_pdf_bytes(pages: int = 1) -> bytes:
    doc = fitz.open()
    for _ in range(pages):
        page = doc.new_page()
        page.insert_text((72, 72), "hello world")
    data = doc.tobytes()
    doc.close()
    return data


def test_valid_pdf_passes() -> None:
    assert probe_pdf_openable(_valid_pdf_bytes()) is None


def test_encrypted_pdf_rejected() -> None:
    doc = fitz.open()
    doc.new_page()
    data = doc.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="user")
    doc.close()
    message = probe_pdf_openable(data)
    assert message is not None
    assert "encrypt" in message.lower() or "password" in message.lower()


def test_pdf_header_wrapping_garbage_rejected() -> None:
    # Passes the endpoints' %PDF magic-byte check but is not a real document.
    message = probe_pdf_openable(b"%PDF-1.7\nthis is not really a pdf at all\n")
    assert message is not None


def test_empty_bytes_rejected() -> None:
    assert probe_pdf_openable(b"") is not None
