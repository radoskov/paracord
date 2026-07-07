"""Path-traversal probes (Batch S): the file-path resolver must keep every resolved path inside its
configured root — rejecting ``../`` escapes, absolute paths outside the root, and symlink escapes —
and the derived-OCR path must reject a non-digest name. The managed-PDF stream endpoint must return
403 for a stored location that escapes the managed root via ``..``.
"""

from __future__ import annotations

import pytest
from app.core.config import Settings
from app.models.file import File, Location
from app.services.file_paths import (
    FileLocationError,
    _validated_path,
    derived_ocr_path,
    resolve_backend_readable_pdf_path,
)

pytestmark = pytest.mark.safety

_TINY_PDF = b"%PDF-1.4\n%%EOF\n"


def test_validated_path_accepts_in_root(tmp_path) -> None:
    root = tmp_path / "root"
    inside = root / "a" / "b.pdf"
    inside.parent.mkdir(parents=True)
    inside.write_bytes(_TINY_PDF)
    resolved = _validated_path(str(inside), root=root, escape_msg="escape")
    assert resolved == inside.resolve()


@pytest.mark.parametrize(
    "suffix",
    ["../../../../etc/passwd", "a/../../outside.pdf", "./../../secret"],
)
def test_validated_path_rejects_dotdot_escape(tmp_path, suffix: str) -> None:
    root = tmp_path / "root"
    root.mkdir()
    with pytest.raises(FileLocationError) as exc:
        _validated_path(str(root / suffix), root=root, escape_msg="escape")
    assert exc.value.kind == "forbidden"


def test_validated_path_rejects_absolute_outside(tmp_path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    with pytest.raises(FileLocationError) as exc:
        _validated_path("/etc/passwd", root=root, escape_msg="escape")
    assert exc.value.kind == "forbidden"


def test_validated_path_rejects_symlink_escape(tmp_path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(_TINY_PDF)
    link = root / "link.pdf"
    link.symlink_to(outside)
    # resolve() follows the symlink to the real (out-of-root) target → rejected.
    with pytest.raises(FileLocationError) as exc:
        _validated_path(str(link), root=root, escape_msg="escape")
    assert exc.value.kind == "forbidden"


@pytest.mark.parametrize("bad", ["short", "x" * 63, "y" * 65, "../../etc/passwd"])
def test_derived_ocr_path_rejects_non_digest(bad: str) -> None:
    with pytest.raises(ValueError):
        derived_ocr_path(Settings(), bad)


def test_derived_ocr_path_stays_under_managed_root(tmp_path) -> None:
    settings = Settings(managed_library_root=str(tmp_path))
    sha = "a" * 64
    path = derived_ocr_path(settings, sha)
    assert path.resolve().is_relative_to(tmp_path.resolve())


def test_resolve_backend_readable_rejects_managed_escape(db, tmp_path) -> None:
    managed_root = tmp_path / "managed"
    managed_root.mkdir()
    escape_uri = str(managed_root / ".." / "outside.pdf")  # escapes via ..
    file = File(sha256="3" * 64, size_bytes=10, mime_type="application/pdf", original_filename="x")
    db.add(file)
    db.flush()
    db.add(
        Location(
            file_id=file.id,
            location_type="managed_path",
            internal_uri=escape_uri,
            display_path="x.pdf",
            is_available=True,
            is_primary=True,
        )
    )
    db.commit()
    with pytest.raises(FileLocationError) as exc:
        resolve_backend_readable_pdf_path(
            db, file=file, settings=Settings(managed_library_root=str(managed_root))
        )
    assert exc.value.kind == "forbidden"


def test_managed_stream_endpoint_rejects_dotdot_escape(
    client, auth_headers, db, monkeypatch, tmp_path
) -> None:
    managed_root = tmp_path / "managed"
    managed_root.mkdir()
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(_TINY_PDF)
    monkeypatch.setattr(
        "app.api.v1.endpoints.files.get_settings",
        lambda: Settings(managed_library_root=str(managed_root)),
    )
    file = File(
        sha256="4" * 64,
        size_bytes=outside.stat().st_size,
        mime_type="application/pdf",
        original_filename="outside.pdf",
    )
    db.add(file)
    db.flush()
    db.add(
        Location(
            file_id=file.id,
            location_type="managed_path",
            # A traversal payload in the stored path must not escape the managed root.
            internal_uri=str(managed_root / "sub" / ".." / ".." / "outside.pdf"),
            display_path="outside.pdf",
            is_available=True,
            is_primary=True,
        )
    )
    db.commit()
    resp = client.get(f"/api/v1/files/{file.id}/stream", headers=auth_headers("reader"))
    assert resp.status_code == 403
