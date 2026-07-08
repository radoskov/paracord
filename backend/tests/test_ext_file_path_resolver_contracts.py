"""Additional backend-readable file path resolver contract tests."""

from __future__ import annotations

import pytest
from app.core.config import Settings
from app.models.file import File, Location
from app.models.source import Source
from app.services.file_paths import (
    FileLocationError,
    derived_ocr_path,
    resolve_backend_readable_pdf_path,
    resolve_streamable_pdf_path,
)

_TINY_PDF = b"%PDF-1.4\n%%EOF\n"


def _file(db, sha: str = "a" * 64) -> File:
    file = File(
        sha256=sha,
        size_bytes=len(_TINY_PDF),
        mime_type="application/pdf",
        original_filename="paper.pdf",
    )
    db.add(file)
    db.flush()
    return file


def test_managed_path_resolver_ignores_unavailable_primary_location(
    db,
    tmp_path,
) -> None:
    managed_root = tmp_path / "managed"
    good_pdf = managed_root / "aa" / "paper.pdf"
    bad_pdf = tmp_path / "outside.pdf"
    good_pdf.parent.mkdir(parents=True)
    good_pdf.write_bytes(_TINY_PDF)
    bad_pdf.write_bytes(_TINY_PDF)

    file = _file(db, "a" * 64)
    db.add_all(
        [
            Location(
                file_id=file.id,
                location_type="managed_path",
                internal_uri=str(bad_pdf),
                is_available=False,
                is_primary=True,
            ),
            Location(
                file_id=file.id,
                location_type="managed_path",
                internal_uri=str(good_pdf),
                is_available=True,
                is_primary=False,
            ),
        ]
    )
    db.commit()

    resolved = resolve_backend_readable_pdf_path(
        db,
        file=file,
        settings=Settings(managed_library_root=str(managed_root)),
    )

    assert resolved == good_pdf.resolve()


def test_server_path_requires_active_server_folder_source(db, tmp_path) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    pdf = source_root / "paper.pdf"
    pdf.write_bytes(_TINY_PDF)

    source = Source(
        type="server_folder",
        name="inactive source",
        is_active=False,
        config={"root_path": str(source_root)},
    )
    file = _file(db, "b" * 64)
    db.add(source)
    db.flush()
    db.add(
        Location(
            file_id=file.id,
            source_id=source.id,
            location_type="server_path",
            internal_uri=str(pdf),
            is_available=True,
            is_primary=True,
        )
    )
    db.commit()

    with pytest.raises(FileLocationError, match="not available"):
        resolve_backend_readable_pdf_path(
            db,
            file=file,
            settings=Settings(managed_library_root=str(tmp_path / "managed")),
        )


def test_streaming_derived_ocr_copy_requires_original_location_to_be_authorized(
    db,
    tmp_path,
) -> None:
    managed_root = tmp_path / "managed"
    managed_root.mkdir()
    outside_pdf = tmp_path / "outside.pdf"
    outside_pdf.write_bytes(_TINY_PDF)

    sha = "c" * 64
    derived = derived_ocr_path(Settings(managed_library_root=str(managed_root)), sha)
    derived.parent.mkdir(parents=True)
    derived.write_bytes(_TINY_PDF)

    file = _file(db, sha)
    db.add(
        Location(
            file_id=file.id,
            location_type="managed_path",
            internal_uri=str(outside_pdf),
            is_available=True,
            is_primary=True,
        )
    )
    db.commit()

    with pytest.raises(FileLocationError) as excinfo:
        resolve_streamable_pdf_path(
            db,
            file=file,
            settings=Settings(managed_library_root=str(managed_root)),
        )

    assert excinfo.value.kind == "forbidden"
