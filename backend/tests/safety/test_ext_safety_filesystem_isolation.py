"""Safety-marked filesystem-isolation probes.

These are adversarial path/derived-file checks. They are intentionally marked as
``safety`` and are meant for pre-push / release hardening, not the fast feature loop.
"""

from __future__ import annotations

import pytest
from app.core.config import Settings
from app.models.file import File, Location
from app.services.file_paths import FileLocationError, resolve_streamable_pdf_path

pytestmark = pytest.mark.safety

_TINY_PDF = b"%PDF-1.4\n%%EOF\n"


def test_derived_pdf_does_not_authorize_an_escaped_original_location(db, tmp_path) -> None:
    managed_root = tmp_path / "managed"
    managed_root.mkdir()
    outside = tmp_path / "sensitive.pdf"
    outside.write_bytes(_TINY_PDF)

    file = File(
        sha256="d" * 64,
        size_bytes=len(_TINY_PDF),
        mime_type="application/pdf",
        original_filename="escaped.pdf",
    )
    db.add(file)
    db.flush()
    db.add(
        Location(
            file_id=file.id,
            location_type="managed_path",
            internal_uri=str(outside),
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


def test_stream_endpoint_requires_auth_even_for_known_file_id(client, db) -> None:
    file = File(
        sha256="e" * 64,
        size_bytes=len(_TINY_PDF),
        mime_type="application/pdf",
        original_filename="paper.pdf",
    )
    db.add(file)
    db.commit()

    response = client.get(f"/api/v1/files/{file.id}/stream")

    assert response.status_code == 401
