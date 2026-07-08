"""Upload-abuse probes (Batch S): the PDF upload surface must reject oversized bodies (413),
non-PDF content-types (400), a malformed / zero-byte / non-%PDF payload (400), and a
decompression-bomb-style body must be bounded by the read cap (413) rather than fully buffered —
all handled gracefully with a clear error and no crash/hang.
"""

from __future__ import annotations

import io

import pytest
from app.api.v1.endpoints import imports as imports_ep

pytestmark = pytest.mark.safety


def _upload(client, headers, data: bytes, content_type: str = "application/pdf"):
    return client.post(
        "/api/v1/imports/upload",
        headers=headers,
        files={"file": ("paper.pdf", io.BytesIO(data), content_type)},
    )


def test_oversized_upload_413(client, auth_headers, monkeypatch) -> None:
    # Shrink the cap so we don't have to build a 200 MB body; the read is bounded to cap+1.
    monkeypatch.setattr(imports_ep, "_MAX_UPLOAD_BYTES", 16)
    body = b"%PDF-1.4" + b"0" * 64  # well over the shrunk cap
    resp = _upload(client, auth_headers("editor"), body)
    assert resp.status_code == 413


def test_non_pdf_content_type_400(client, auth_headers) -> None:
    resp = _upload(client, auth_headers("editor"), b"<html>hi</html>", content_type="text/html")
    assert resp.status_code == 400
    assert "pdf" in resp.json()["detail"].lower()


def test_malformed_pdf_rejected_400(client, auth_headers) -> None:
    resp = _upload(client, auth_headers("editor"), b"NOT-A-PDF-AT-ALL")
    assert resp.status_code == 400


def test_zero_byte_upload_rejected_400(client, auth_headers) -> None:
    resp = _upload(client, auth_headers("editor"), b"")
    assert resp.status_code == 400


def test_truncated_pdf_header_rejected_400(client, auth_headers) -> None:
    resp = _upload(client, auth_headers("editor"), b"%PD")  # < 4 bytes / not %PDF
    assert resp.status_code == 400


def test_pdf_header_but_unopenable_body_rejected_400(client, auth_headers) -> None:
    # E2: a valid %PDF magic prefix wrapping non-parseable bytes clears the header check but must
    # be rejected by the parser-level probe (encrypted/corrupt) before it can reach GROBID/OCR.
    resp = _upload(client, auth_headers("editor"), b"%PDF-1.7\nnot actually a real pdf body\n")
    assert resp.status_code == 400
    detail = resp.json()["detail"].lower()
    assert any(s in detail for s in ("could not be opened", "no pages", "encrypt"))


def test_decompression_bomb_style_body_is_bounded(client, auth_headers, monkeypatch) -> None:
    # A "bomb"-style body (huge, would expand on decompression) is never fully buffered: the read
    # cap trips 413 first. We prove the bound by shrinking the cap and sending far more than it.
    monkeypatch.setattr(imports_ep, "_MAX_UPLOAD_BYTES", 1024)
    body = b"%PDF-1.4" + b"\x00" * (4096)
    resp = _upload(client, auth_headers("editor"), body)
    assert resp.status_code == 413
    # The endpoint stayed responsive (returned promptly, no hang) — a subsequent request still works.
    assert client.get("/api/v1/health").status_code == 200
