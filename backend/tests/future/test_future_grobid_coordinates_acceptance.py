"""Future acceptance tests for GROBID coordinates and citation contexts."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.skip(reason="future stage: GROBID coordinate-aware extraction")


def test_extraction_stores_citation_contexts_with_pdf_coordinates(client, auth_headers) -> None:
    headers = auth_headers("editor")

    upload = client.post(
        "/api/v1/imports/upload",
        headers=headers,
        files={"file": ("paper.pdf", b"%PDF-1.4\n%%EOF\n", "application/pdf")},
    )
    assert upload.status_code == 201

    files = client.get("/api/v1/files", headers=headers).json()
    assert len(files) == 1

    extract = client.post(f"/api/v1/files/{files[0]['id']}/extract", headers=headers)
    assert extract.status_code == 202

    works = client.get("/api/v1/works", headers=headers).json()
    contexts = client.get(
        f"/api/v1/works/{works[0]['id']}/citation-contexts", headers=headers
    ).json()
    assert contexts
    assert all(context["page"] is not None for context in contexts)
    assert all(context["context_sentence"] for context in contexts)
    assert all("pdf_x" in context for context in contexts)
