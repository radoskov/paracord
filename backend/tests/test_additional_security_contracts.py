"""Additional security-contract tests for the current PaRacORD stage.

These tests intentionally exercise stable product invariants, not implementation
minutiae:

* disabled and expired sessions cannot authenticate;
* raw bearer tokens are never stored as-is;
* read-only users cannot start import/upload/enrichment style writes;
* managed-library PDF streaming respects the configured managed root.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime

import pytest
from app.core.config import Settings
from app.models.file import File, Location
from app.services.auth import create_user_session, get_active_session, hash_token

# Supplementary contract coverage (see module docstring) — excluded from `make test`/`make ready`;
# run via `make test-full`/`make ready-full` or `pytest -m slow`.
pytestmark = pytest.mark.slow

_TINY_PDF = b"%PDF-1.4\n%%EOF\n"


def test_disabled_user_token_is_rejected_by_api(client, db, make_user) -> None:
    user = make_user("disabled-user", role="reader")
    token, _session = create_user_session(db, user, ttl_minutes=60)
    user.disabled_at = datetime.now(UTC)
    db.commit()

    response = client.get("/api/v1/works", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401


def test_expired_user_session_is_rejected_by_api(client, db, make_user) -> None:
    user = make_user("expired-user", role="reader")
    token, _session = create_user_session(db, user, ttl_minutes=-1)
    db.commit()

    response = client.get("/api/v1/works", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401


def test_user_session_stores_only_token_hash(db, make_user) -> None:
    user = make_user("token-storage-user", role="reader")
    token, session = create_user_session(db, user, ttl_minutes=60)
    db.commit()
    db.refresh(session)

    assert session.token_hash != token
    assert session.token_hash == hash_token(token)
    assert get_active_session(db, token).id == session.id
    assert get_active_session(db, session.token_hash) is None


def test_reader_cannot_start_import_or_upload_flows(client, auth_headers) -> None:
    headers = auth_headers("reader")

    assert (
        client.post(
            "/api/v1/imports/bibtex",
            headers=headers,
            json={"content": "@article{a, title={A}}"},
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/v1/imports/identifier",
            headers=headers,
            json={"identifier_type": "arxiv", "value": "1706.03762"},
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/v1/imports/upload",
            headers=headers,
            files={"file": ("paper.pdf", io.BytesIO(_TINY_PDF), "application/pdf")},
        ).status_code
        == 403
    )


def test_managed_pdf_stream_accepts_files_inside_managed_root(
    client,
    auth_headers,
    db,
    monkeypatch,
    tmp_path,
) -> None:
    managed_root = tmp_path / "managed"
    pdf_path = managed_root / "ab" / "cd" / "inside.pdf"
    pdf_path.parent.mkdir(parents=True)
    pdf_path.write_bytes(_TINY_PDF)

    monkeypatch.setattr(
        "app.api.v1.endpoints.files.get_settings",
        lambda: Settings(managed_library_root=str(managed_root)),
    )

    file = File(
        sha256="1" * 64,
        size_bytes=pdf_path.stat().st_size,
        mime_type="application/pdf",
        original_filename="inside.pdf",
    )
    db.add(file)
    db.flush()
    db.add(
        Location(
            file_id=file.id,
            location_type="managed_path",
            internal_uri=str(pdf_path),
            display_path="inside.pdf",
            is_available=True,
            is_primary=True,
        )
    )
    db.commit()

    response = client.get(f"/api/v1/files/{file.id}/stream", headers=auth_headers("reader"))

    assert response.status_code == 200
    assert response.content.startswith(b"%PDF")


def test_managed_pdf_stream_rejects_files_outside_managed_root(
    client,
    auth_headers,
    db,
    monkeypatch,
    tmp_path,
) -> None:
    managed_root = tmp_path / "managed"
    managed_root.mkdir()
    outside_pdf = tmp_path / "outside.pdf"
    outside_pdf.write_bytes(_TINY_PDF)

    monkeypatch.setattr(
        "app.api.v1.endpoints.files.get_settings",
        lambda: Settings(managed_library_root=str(managed_root)),
    )

    file = File(
        sha256="2" * 64,
        size_bytes=outside_pdf.stat().st_size,
        mime_type="application/pdf",
        original_filename="outside.pdf",
    )
    db.add(file)
    db.flush()
    db.add(
        Location(
            file_id=file.id,
            location_type="managed_path",
            internal_uri=str(outside_pdf),
            display_path="outside.pdf",
            is_available=True,
            is_primary=True,
        )
    )
    db.commit()

    response = client.get(f"/api/v1/files/{file.id}/stream", headers=auth_headers("reader"))

    assert response.status_code == 403
