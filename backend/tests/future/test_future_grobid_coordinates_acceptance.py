"""Acceptance test for GROBID coordinate-aware citation contexts (WORKPLAN Stage 2 / B1).

Originally a skipped placeholder that required a live GROBID + worker. It is now a
deterministic acceptance test: extraction is driven from a coordinate-bearing TEI fixture
through the real ``extract_and_store`` service (the same code the worker runs), then the
citation contexts are read back through the HTTP API to assert the page + PDF coordinate
contract the PDF.js reader anchors to.
"""

from __future__ import annotations

from pathlib import Path

from app.models.file import File, FileWorkLink, Location
from app.models.work import Work
from app.services.extraction import extract_and_store

FIXTURE = (Path(__file__).parent.parent / "fixtures" / "minimal_grobid_tei.xml").read_text(
    encoding="utf-8"
)


def test_extraction_stores_citation_contexts_with_pdf_coordinates(
    client, auth_headers, db, tmp_path
) -> None:
    from app.core.config import get_settings

    # Seed a work + file with a managed-library location (the path is never read because the
    # TEI fetcher is injected; the resolver only validates it lives under the managed root).
    managed_root = tmp_path / "library"
    pdf_path = managed_root / "ab" / "cd" / "paper.pdf"
    work = Work(canonical_title="paper", normalized_title="paper")
    file = File(sha256="f" * 64, size_bytes=14, mime_type="application/pdf")
    db.add_all([work, file])
    db.flush()
    db.add_all(
        [
            FileWorkLink(file_id=file.id, work_id=work.id),
            Location(
                file_id=file.id,
                source_id=None,
                location_type="managed_path",
                internal_uri=str(pdf_path),
            ),
        ]
    )
    db.flush()

    settings = get_settings().model_copy(update={"managed_library_root": str(managed_root)})
    extract_and_store(db, file=file, fetch_tei=lambda _p: FIXTURE, settings=settings)
    db.commit()

    contexts = client.get(
        f"/api/v1/works/{work.id}/citation-contexts", headers=auth_headers("reader")
    ).json()

    assert contexts
    assert all(context["page"] is not None for context in contexts)
    assert all(context["context_sentence"] for context in contexts)
    assert all("pdf_x" in context for context in contexts)
    # The first marker resolves to a single coordinate box on page 3.
    first = next(c for c in contexts if c["marker_text"] == "[1]")
    assert first["page"] == 3
    assert first["pdf_x"] == 123.4
    assert first["pdf_coordinates"] == [{"page": 3, "x": 123.4, "y": 456.7, "w": 12.0, "h": 10.5}]
