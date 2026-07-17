"""``POST /works/{id}/files/from-path`` — attach a PDF already on the server's filesystem.

The path must resolve (symlinks followed) to a file INSIDE one of the merged allowed server roots
(``server.yaml`` + owner-managed ``import_roots`` rows); the bytes then pass the exact same
validation as a browser upload. These tests drive the endpoint through the GUI-managed DB roots
(the yaml and DB entries merge through the same ``merged_server_roots`` helper, covered by
``test_import_roots.py``).
"""

from __future__ import annotations

import fitz
import pytest
from app.models.import_root import ImportRoot


def _real_pdf_bytes() -> bytes:
    """A real, openable single-page PDF (the E2 upload probe rejects header-only stubs)."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "from-path attach fixture")
    data = doc.tobytes()
    doc.close()
    return data


_PDF_BYTES = _real_pdf_bytes()


@pytest.fixture()
def allowed_root(db, tmp_path):
    """A tmp directory registered as a GUI-managed allowed server root."""
    root = tmp_path / "allowed"
    root.mkdir()
    db.add(ImportRoot(alias="test-root", path=str(root)))
    db.commit()
    return root


def _make_work(client, headers, title: str) -> str:
    resp = client.post("/api/v1/works", headers=headers, json={"canonical_title": title})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def test_attach_from_path_inside_root(client, auth_headers, allowed_root):
    """A PDF inside an allowed root attaches: 201, file linked, visible in the work's file list."""
    h = auth_headers("editor")
    pdf = allowed_root / "paper.pdf"
    pdf.write_bytes(_PDF_BYTES)
    work_id = _make_work(client, h, "From-path paper")

    resp = client.post(
        f"/api/v1/works/{work_id}/files/from-path", headers=h, json={"path": str(pdf)}
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["original_filename"] == "paper.pdf"

    files = client.get(f"/api/v1/works/{work_id}/files", headers=h).json()
    assert [f["id"] for f in files] == [body["id"]]


def test_attach_from_path_dedupes_by_content(client, auth_headers, allowed_root):
    """The same file attached to a second paper reuses the stored file (content-addressed)."""
    h = auth_headers("editor")
    pdf = allowed_root / "shared.pdf"
    pdf.write_bytes(_PDF_BYTES)
    first = _make_work(client, h, "From-path first")
    second = _make_work(client, h, "From-path second")

    id_a = client.post(
        f"/api/v1/works/{first}/files/from-path", headers=h, json={"path": str(pdf)}
    ).json()["id"]
    resp_b = client.post(
        f"/api/v1/works/{second}/files/from-path", headers=h, json={"path": str(pdf)}
    )
    assert resp_b.status_code == 201
    assert resp_b.json()["id"] == id_a  # same File row, second FileWorkLink


def test_attach_from_path_outside_root_is_refused(client, auth_headers, allowed_root, tmp_path):
    """A path outside every allowed root is refused up front (403), attaching nothing."""
    h = auth_headers("editor")
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(_PDF_BYTES)
    work_id = _make_work(client, h, "From-path outside")

    resp = client.post(
        f"/api/v1/works/{work_id}/files/from-path", headers=h, json={"path": str(outside)}
    )
    assert resp.status_code == 403
    assert "allowed server folder" in resp.json()["detail"]
    assert client.get(f"/api/v1/works/{work_id}/files", headers=h).json() == []


def test_attach_from_path_symlink_escape_is_refused(client, auth_headers, allowed_root, tmp_path):
    """A symlink inside a root pointing outside it is refused: containment runs on the resolved target."""
    h = auth_headers("editor")
    outside = tmp_path / "escape-target.pdf"
    outside.write_bytes(_PDF_BYTES)
    link = allowed_root / "innocent-looking.pdf"
    link.symlink_to(outside)
    work_id = _make_work(client, h, "From-path symlink")

    resp = client.post(
        f"/api/v1/works/{work_id}/files/from-path", headers=h, json={"path": str(link)}
    )
    assert resp.status_code == 403


def test_attach_from_path_traversal_is_refused(client, auth_headers, allowed_root, tmp_path):
    """`..` segments cannot climb out of a root (the resolved path is what gets checked)."""
    h = auth_headers("editor")
    outside = tmp_path / "climbed.pdf"
    outside.write_bytes(_PDF_BYTES)
    work_id = _make_work(client, h, "From-path traversal")

    sneaky = f"{allowed_root}/../{outside.name}"
    resp = client.post(f"/api/v1/works/{work_id}/files/from-path", headers=h, json={"path": sneaky})
    assert resp.status_code == 403


def test_attach_from_path_missing_file_404(client, auth_headers, allowed_root):
    h = auth_headers("editor")
    work_id = _make_work(client, h, "From-path missing")
    resp = client.post(
        f"/api/v1/works/{work_id}/files/from-path",
        headers=h,
        json={"path": str(allowed_root / "nope.pdf")},
    )
    assert resp.status_code == 404
    assert "No file exists" in resp.json()["detail"]


def test_attach_from_path_directory_404(client, auth_headers, allowed_root):
    """The root itself (a directory) is not a file — folder imports go through Sources."""
    h = auth_headers("editor")
    work_id = _make_work(client, h, "From-path dir")
    resp = client.post(
        f"/api/v1/works/{work_id}/files/from-path", headers=h, json={"path": str(allowed_root)}
    )
    assert resp.status_code == 404


def test_attach_from_path_non_pdf_400(client, auth_headers, allowed_root):
    h = auth_headers("editor")
    junk = allowed_root / "notes.txt"
    junk.write_bytes(b"plain text, not a pdf")
    work_id = _make_work(client, h, "From-path non-pdf")
    resp = client.post(
        f"/api/v1/works/{work_id}/files/from-path", headers=h, json={"path": str(junk)}
    )
    assert resp.status_code == 400
    assert "not a valid PDF" in resp.json()["detail"]


def test_attach_from_path_blank_400(client, auth_headers, allowed_root):
    h = auth_headers("editor")
    work_id = _make_work(client, h, "From-path blank")
    resp = client.post(f"/api/v1/works/{work_id}/files/from-path", headers=h, json={"path": "   "})
    assert resp.status_code == 400


def test_attach_from_path_requires_contributor(client, auth_headers, allowed_root):
    """Readers can't attach files (same modify guard as a browser upload)."""
    editor = auth_headers("editor")
    pdf = allowed_root / "role.pdf"
    pdf.write_bytes(_PDF_BYTES)
    work_id = _make_work(client, editor, "From-path role")
    resp = client.post(
        f"/api/v1/works/{work_id}/files/from-path",
        headers=auth_headers("reader"),
        json={"path": str(pdf)},
    )
    assert resp.status_code == 403
