"""GROBID TEI parsing and extraction-persistence tests (M2)."""

from pathlib import Path

import pytest
from app.api.v1.endpoints.works import get_work_citation_contexts
from app.db.base import Base
from app.models.audit import AuditEvent
from app.models.citation import CitationMention, RawTeiDocument, Reference
from app.models.file import File, FileWorkLink, Location
from app.models.metadata import MetadataAssertion
from app.models.source import Source
from app.models.user import User
from app.models.work import Work
from app.services.extraction import extract_and_store, store_parsed_extraction
from app.services.storage import file_ids_pending_extraction
from app.services.tei_parser import parse_tei
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

FIXTURE = (Path(__file__).parent / "fixtures" / "minimal_grobid_tei.xml").read_text(
    encoding="utf-8"
)

# Heavier suite: slow per-test schema setup (full Base.metadata create_all on file-backed SQLite)
# plus GROBID-extraction coverage — moved to the full tier. Run via `make test-full`/`make
# ready-full` or `pytest -m slow`.
pytestmark = pytest.mark.slow


@pytest.fixture()
def db_session(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'extraction.db'}")
    Base.metadata.create_all(
        bind=engine,
        tables=[
            Work.__table__,
            File.__table__,
            FileWorkLink.__table__,
            Location.__table__,
            Source.__table__,
            RawTeiDocument.__table__,
            Reference.__table__,
            CitationMention.__table__,
            MetadataAssertion.__table__,
            AuditEvent.__table__,
        ],
    )
    session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    with session_local() as session:
        yield session


def test_parse_tei_extracts_title_abstract_doi_authors_references() -> None:
    parsed = parse_tei(FIXTURE)
    assert parsed.title == "Attention Is All You Need"
    assert parsed.abstract.startswith("We propose the Transformer")
    assert parsed.doi == "10.5555/transformer"
    assert parsed.authors == ["Ashish Vaswani", "Noam Shazeer"]
    assert len(parsed.references) == 2
    assert len(parsed.citation_mentions) == 2
    first = parsed.references[0]
    assert first.key == "b0"
    assert first.title.startswith("Neural Machine Translation")
    assert first.doi == "10.5555/nmt"
    assert first.year == 2015
    assert parsed.references[1].raw_citation == "Some Unparsed Reference, 2010."
    first_mention = parsed.citation_mentions[0]
    assert first_mention.reference_key == "b0"
    assert first_mention.marker_text == "[1]"
    assert first_mention.section_label == "Introduction"
    assert first_mention.context_before == "Sequence models have long used recurrent layers."
    assert first_mention.context_sentence.endswith("translation quality [1].")
    assert first_mention.context_after.endswith("considered [2].")


def test_parse_tei_handles_empty_and_invalid() -> None:
    assert parse_tei("").references == []
    assert parse_tei("<not-tei>").title is None


def test_parse_tei_extracts_citation_coordinates() -> None:
    parsed = parse_tei(FIXTURE)
    first, second = parsed.citation_mentions
    assert first.page == 3
    assert first.pdf_coordinates == [{"page": 3, "x": 123.4, "y": 456.7, "w": 12.0, "h": 10.5}]
    # A mention can wrap across lines → multiple coordinate boxes.
    assert second.page == 4
    assert len(second.pdf_coordinates) == 2
    assert second.pdf_coordinates[1]["x"] == 100.0


def test_parse_coords_handles_malformed() -> None:
    from app.services.tei_parser import _parse_coords

    assert _parse_coords(None) == []
    assert _parse_coords("") == []
    assert _parse_coords("garbage") == []
    assert _parse_coords("3,1,2,3") == []  # too few parts
    assert _parse_coords("3,1.0,2.0,3.0,4.0") == [
        {"page": 3, "x": 1.0, "y": 2.0, "w": 3.0, "h": 4.0}
    ]


def test_grobid_form_data_reflects_settings() -> None:
    from app.core.config import get_settings
    from app.services.grobid_client import GrobidClient

    settings = get_settings().model_copy(
        update={"grobid_consolidate_header": False, "grobid_coordinate_elements": ["ref", "s"]}
    )
    # _form_data returns a dict (repeated fields as lists) — the shape httpx2's multipart
    # encoder needs; a list of (key, value) tuples gets mangled into bytes errors.
    data = GrobidClient("http://grobid:8070", settings=settings)._form_data()
    assert data["consolidateHeader"] == "0"
    assert data["consolidateCitations"] == "1"
    assert data["teiCoordinates"] == ["ref", "s"]


def test_store_parsed_extraction_promotes_when_not_user_confirmed(db_session) -> None:
    work = Work(
        canonical_title="attention is all you need",
        normalized_title="attention is all you need",
        canonical_metadata_source="filename",
        user_confirmed=False,
    )
    db_session.add(work)
    db_session.commit()

    summary = store_parsed_extraction(db_session, work=work, parsed=parse_tei(FIXTURE))
    db_session.commit()

    assert set(summary["promoted"]) == {"title", "abstract", "doi"}
    assert work.canonical_title == "Attention Is All You Need"
    assert work.canonical_metadata_source == "grobid"
    assert work.abstract.startswith("We propose")
    assert work.doi == "10.5555/transformer"
    assert (
        db_session.scalar(select(Reference).where(Reference.citing_work_id == work.id)) is not None
    )
    assert len(db_session.scalars(select(Reference)).all()) == 2
    assert summary["citation_mention_count"] == 2
    assert len(db_session.scalars(select(CitationMention)).all()) == 2
    title_assertion = db_session.scalar(
        select(MetadataAssertion).where(
            MetadataAssertion.field_name == "title", MetadataAssertion.source == "grobid"
        )
    )
    assert title_assertion.selected_as_canonical is True


def test_store_parsed_extraction_respects_user_confirmed(db_session) -> None:
    work = Work(
        canonical_title="My Curated Title",
        normalized_title="my curated title",
        canonical_metadata_source="user",
        user_confirmed=True,
    )
    db_session.add(work)
    db_session.commit()

    summary = store_parsed_extraction(db_session, work=work, parsed=parse_tei(FIXTURE))
    db_session.commit()

    assert summary["promoted"] == []
    assert work.canonical_title == "My Curated Title"  # unchanged
    assert work.abstract is None  # not overwritten
    # Assertions are still recorded, just not canonical.
    title_assertion = db_session.scalar(
        select(MetadataAssertion).where(MetadataAssertion.field_name == "title")
    )
    assert title_assertion.selected_as_canonical is False


def test_store_parsed_extraction_is_idempotent(db_session) -> None:
    work = Work(canonical_title="x", normalized_title="x", canonical_metadata_source="filename")
    db_session.add(work)
    db_session.commit()
    store_parsed_extraction(db_session, work=work, parsed=parse_tei(FIXTURE))
    db_session.commit()
    store_parsed_extraction(db_session, work=work, parsed=parse_tei(FIXTURE))
    db_session.commit()
    assert len(db_session.scalars(select(Reference)).all()) == 2  # not duplicated
    assert len(db_session.scalars(select(CitationMention)).all()) == 2


def test_extract_and_store_uses_fetcher_location_and_audits(db_session, tmp_path: Path) -> None:
    work = Work(canonical_title="f", normalized_title="f", canonical_metadata_source="filename")
    file = File(sha256="a" * 64, size_bytes=10, mime_type="application/pdf")
    source = Source(
        type="server_folder", name="S", path_alias="s", config={"root_path": str(tmp_path)}
    )
    db_session.add_all([work, file, source])
    db_session.flush()
    db_session.add_all(
        [
            FileWorkLink(file_id=file.id, work_id=work.id),
            Location(
                file_id=file.id,
                source_id=source.id,
                location_type="server_path",
                internal_uri=str(tmp_path / "paper.pdf"),
            ),
        ]
    )
    db_session.commit()

    captured = {}

    def fake_fetch(path: Path) -> str:
        captured["path"] = path
        return FIXTURE

    summary = extract_and_store(db_session, file=file, fetch_tei=fake_fetch)
    db_session.commit()

    assert captured["path"] == tmp_path / "paper.pdf"
    assert summary["reference_count"] == 2
    assert summary["citation_mention_count"] == 2
    assert summary["raw_tei_stored"] is True
    assert work.doi == "10.5555/transformer"
    raw_tei = db_session.scalar(select(RawTeiDocument))
    assert raw_tei.file_id == file.id
    assert raw_tei.work_id == work.id
    assert raw_tei.tei_xml == FIXTURE
    mention = db_session.scalar(select(CitationMention).where(CitationMention.marker_text == "[1]"))
    assert mention.source_tei_id == raw_tei.id
    assert mention.page == 3
    assert mention.pdf_coordinates == [{"page": 3, "x": 123.4, "y": 456.7, "w": 12.0, "h": 10.5}]
    # Admin actor bypasses ACLs (no users table in this narrow schema, so keep it unpersisted).
    actor = User(username="extract-actor", password_hash="x", role="admin")
    contexts = get_work_citation_contexts(work.id, db=db_session, actor=actor)
    assert len(contexts) == 2
    assert contexts[0].reference_title.startswith("Neural Machine Translation")
    assert contexts[0].context_sentence.endswith("translation quality [1].")
    assert db_session.scalars(
        select(AuditEvent).where(AuditEvent.event_type == "extraction.completed")
    ).all()


def test_extract_and_store_requires_readable_location(db_session) -> None:
    work = Work(canonical_title="f", normalized_title="f")
    file = File(sha256="b" * 64, size_bytes=10, mime_type="application/pdf")
    db_session.add_all([work, file])
    db_session.flush()
    db_session.add(FileWorkLink(file_id=file.id, work_id=work.id))
    db_session.commit()

    with pytest.raises(ValueError, match="No readable PDF location"):
        extract_and_store(db_session, file=file, fetch_tei=lambda _p: "")


def test_extract_and_store_reads_managed_path(db_session, tmp_path: Path) -> None:
    """Uploaded managed-library PDFs are extractable, not just server-folder ones (AUDIT A1)."""
    from app.core.config import get_settings
    from app.services.storage import content_addressed_path

    managed_root = tmp_path / "library"
    sha = "e" * 64
    pdf_path = content_addressed_path(managed_root, sha)
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_path.write_bytes(b"%PDF-1.4\n%%EOF\n")

    work = Work(canonical_title="m", normalized_title="m")
    file = File(sha256=sha, size_bytes=14, mime_type="application/pdf")
    db_session.add_all([work, file])
    db_session.flush()
    db_session.add_all(
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
    db_session.commit()

    settings = get_settings().model_copy(update={"managed_library_root": str(managed_root)})
    captured: dict[str, Path] = {}

    def fake_fetch(path: Path) -> str:
        captured["path"] = path
        return FIXTURE

    summary = extract_and_store(db_session, file=file, fetch_tei=fake_fetch, settings=settings)
    db_session.commit()

    assert captured["path"] == pdf_path.resolve()
    assert summary["reference_count"] == 2
    assert summary["raw_tei_stored"] is True


def _make_extractable_file(db_session, tmp_path: Path, *, quality: str):
    """A work + file + server-path location whose PDF exists on disk, ready for extract_and_store."""
    from app.core.config import get_settings

    work = Work(canonical_title="o", normalized_title="o", canonical_metadata_source="filename")
    file = File(
        sha256="c" * 64, size_bytes=10, mime_type="application/pdf", text_layer_quality=quality
    )
    source = Source(
        type="server_folder", name="S", path_alias="s", config={"root_path": str(tmp_path)}
    )
    db_session.add_all([work, file, source])
    db_session.flush()
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")
    db_session.add_all(
        [
            FileWorkLink(file_id=file.id, work_id=work.id),
            Location(
                file_id=file.id,
                source_id=source.id,
                location_type="server_path",
                internal_uri=str(pdf),
            ),
        ]
    )
    db_session.commit()
    # ocr_backend defaults to "ocrmypdf" (no ai_config table in this narrow schema → Settings).
    settings = get_settings()
    return work, file, pdf, settings


def test_extract_runs_ocr_on_poor_text_layer(db_session, tmp_path: Path, monkeypatch) -> None:
    from app.services import ocr as ocr_service

    work, file, pdf, settings = _make_extractable_file(db_session, tmp_path, quality="poor")
    ocr_pdf = tmp_path / "paper.ocr.pdf"
    ocr_pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")

    def fake_maybe_ocr(path, **_kw):
        assert path == pdf.resolve()
        return ocr_service.OcrResult(
            ocr_pdf, ran=True, engine="ocrmypdf", text_layer_quality="ocr_added", error=None
        )

    monkeypatch.setattr(ocr_service, "maybe_ocr", fake_maybe_ocr)

    fed: dict[str, Path] = {}

    def fake_fetch(path: Path) -> str:
        fed["path"] = path
        return FIXTURE

    summary = extract_and_store(db_session, file=file, fetch_tei=fake_fetch, settings=settings)
    db_session.commit()

    assert fed["path"] == ocr_pdf  # GROBID fed the OCR'd copy, not the original
    assert summary["ocr_ran"] is True
    assert summary["ocr_engine"] == "ocrmypdf"
    assert file.text_layer_quality == "ocr_added"  # wins over the post-GROBID recompute


def test_extract_skips_ocr_on_good_text_layer(db_session, tmp_path: Path, monkeypatch) -> None:
    from app.services import ocr as ocr_service

    work, file, pdf, settings = _make_extractable_file(db_session, tmp_path, quality="good")

    def fake_maybe_ocr(*_a, **_k):  # pragma: no cover - must not be called for a good layer
        raise AssertionError("OCR should not run when text layer is good")

    monkeypatch.setattr(ocr_service, "maybe_ocr", fake_maybe_ocr)

    fed: dict[str, Path] = {}

    def fake_fetch(path: Path) -> str:
        fed["path"] = path
        return FIXTURE

    summary = extract_and_store(db_session, file=file, fetch_tei=fake_fetch, settings=settings)
    db_session.commit()

    assert fed["path"] == pdf.resolve()  # original PDF fed to GROBID
    assert summary["ocr_ran"] is False


def test_extract_ocr_failure_does_not_fail_extraction(
    db_session, tmp_path: Path, monkeypatch
) -> None:
    from app.services import ocr as ocr_service

    work, file, pdf, settings = _make_extractable_file(db_session, tmp_path, quality="none")

    # maybe_ocr swallows the failure and returns the ORIGINAL path with an error string.
    def fake_maybe_ocr(path, **_kw):
        return ocr_service.OcrResult(
            path, ran=False, engine="ocrmypdf", text_layer_quality=None, error="ocr blew up"
        )

    monkeypatch.setattr(ocr_service, "maybe_ocr", fake_maybe_ocr)

    fed: dict[str, Path] = {}

    def fake_fetch(path: Path) -> str:
        fed["path"] = path
        return FIXTURE

    summary = extract_and_store(db_session, file=file, fetch_tei=fake_fetch, settings=settings)
    db_session.commit()

    # Extraction still succeeded on the original PDF, provenance records the swallowed error, and
    # the completion audit is still emitted.
    assert fed["path"] == pdf.resolve()
    assert summary["ocr_ran"] is False
    assert summary["ocr_error"] == "ocr blew up"
    assert summary["reference_count"] == 2
    assert db_session.scalars(
        select(AuditEvent).where(AuditEvent.event_type == "extraction.completed")
    ).all()


def test_file_ids_pending_extraction(db_session) -> None:
    from app.models.metadata import MetadataAssertion

    source = Source(type="server_folder", name="S", path_alias="s", config={"root_path": "/x"})
    work = Work(canonical_title="w", normalized_title="w")
    file = File(sha256="d" * 64, size_bytes=1, mime_type="application/pdf")
    db_session.add_all([source, work, file])
    db_session.flush()
    db_session.add_all(
        [
            FileWorkLink(file_id=file.id, work_id=work.id),
            Location(file_id=file.id, source_id=source.id, location_type="server_path"),
        ]
    )
    db_session.commit()

    assert file_ids_pending_extraction(db_session, source.id) == [file.id]

    db_session.add(
        MetadataAssertion(
            entity_type="work", entity_id=work.id, field_name="title", value="t", source="grobid"
        )
    )
    db_session.commit()
    assert file_ids_pending_extraction(db_session, source.id) == []


def test_extraction_stores_citation_contexts_with_pdf_coordinates(
    client, auth_headers, db, tmp_path
) -> None:
    """Coordinate-aware acceptance: drive extraction from the coordinate-bearing TEI fixture through
    the real ``extract_and_store`` service (what the worker runs), then read the citation contexts
    back through the HTTP API to assert the page + PDF-coordinate contract the PDF.js reader anchors
    to."""
    from app.core.config import get_settings

    # Seed a work + file with a managed-library location (the path is never read because the TEI
    # fetcher is injected; the resolver only validates it lives under the managed root).
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


def test_extract_routes_to_pymupdf_backend(db_session, tmp_path, monkeypatch) -> None:
    """ocr_backend="pymupdf" feeds a PyMuPDF-OCR'd searchable copy to GROBID (like ocrmypdf)."""
    from app.core.config import get_settings
    from app.services import ocr as ocr_service

    work, file, pdf, _ = _make_extractable_file(db_session, tmp_path, quality="poor")
    settings = get_settings().model_copy(update={"ocr_backend": "pymupdf"})
    monkeypatch.setattr(ocr_service, "pymupdf_available", lambda: True)
    # Guard: the ocrmypdf engine must NOT run when pymupdf is selected.
    monkeypatch.setattr(
        ocr_service,
        "maybe_ocr",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no ocrmypdf")),
    )
    ocr_pdf = tmp_path / "paper.pymupdf.ocr.pdf"
    ocr_pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")

    seen: dict = {}

    def fake_pymupdf_ocr(path, **kw):
        seen["path"] = path
        seen["language"] = kw.get("language")
        return ocr_service.OcrResult(
            ocr_pdf, ran=True, engine="pymupdf", text_layer_quality="ocr_added", error=None
        )

    monkeypatch.setattr(ocr_service, "pymupdf_ocr", fake_pymupdf_ocr)

    fed: dict = {}

    def fake_fetch(path):
        fed["path"] = path
        return FIXTURE

    summary = extract_and_store(db_session, file=file, fetch_tei=fake_fetch, settings=settings)
    db_session.commit()

    assert seen["path"] == pdf.resolve()  # OCR ran on the ORIGINAL PDF
    assert fed["path"] == ocr_pdf  # GROBID fed the searchable copy
    assert summary["ocr_ran"] is True
    assert summary["ocr_engine"] == "pymupdf"
    assert summary["ocr_backend"] == "pymupdf"
    assert summary["ocr_available"] is True
    assert file.text_layer_quality == "ocr_added"


def test_extract_persists_derived_ocr_pdf(db_session, tmp_path, monkeypatch) -> None:
    """When OCR produces a searchable copy, its bytes are persisted to the derived location."""
    from app.core.config import get_settings
    from app.services import ocr as ocr_service
    from app.services.file_paths import derived_ocr_path

    work, file, pdf, _ = _make_extractable_file(db_session, tmp_path, quality="poor")
    managed_root = tmp_path / "lib"
    settings = get_settings().model_copy(update={"managed_library_root": str(managed_root)})
    ocr_pdf = tmp_path / "paper.ocr.pdf"
    ocr_pdf.write_bytes(b"%PDF-1.4\n% searchable ocr copy\n%%EOF\n")

    def fake_maybe_ocr(path, **_kw):
        return ocr_service.OcrResult(
            ocr_pdf, ran=True, engine="ocrmypdf", text_layer_quality="ocr_added", error=None
        )

    monkeypatch.setattr(ocr_service, "maybe_ocr", fake_maybe_ocr)
    monkeypatch.setattr(ocr_service, "ocrmypdf_available", lambda: True)

    extract_and_store(db_session, file=file, fetch_tei=lambda _p: FIXTURE, settings=settings)
    db_session.commit()

    derived = derived_ocr_path(settings, file.sha256)
    assert derived.exists()
    assert derived.read_bytes() == ocr_pdf.read_bytes()  # the searchable copy, byte-for-byte


def test_extract_full_ml_enriches_with_pymupdf_hard_text(db_session, tmp_path, monkeypatch) -> None:
    """full_ml route runs the PyMuPDF hard extractor and enriches keywords when GROBID body is weak."""
    from app.core.config import get_settings
    from app.services import ocr as ocr_service

    # A minimal TEI with a title but no abstract/body → without extra_text, no keywords.
    weak_tei = (
        "<TEI xmlns='http://www.tei-c.org/ns/1.0'><teiHeader><fileDesc><titleStmt>"
        "<title>Scanned Paper</title></titleStmt></fileDesc></teiHeader>"
        "<text><body></body></text></TEI>"
    )
    work, file, pdf, _ = _make_extractable_file(db_session, tmp_path, quality="good")
    settings = get_settings().model_copy(update={"ocr_backend": "full_ml"})

    called = {}

    def fake_run_ml(path, *, backend, language="eng"):
        called["backend"] = backend
        return "reinforcement learning policy gradient optimization for robotics control systems"

    monkeypatch.setattr(ocr_service, "run_ml_extraction", fake_run_ml)

    extract_and_store(db_session, file=file, fetch_tei=lambda _p: weak_tei, settings=settings)
    db_session.commit()

    assert called["backend"] == "pymupdf"  # the shipped hard extractor, not nougat/marker
    assert work.keywords  # enriched from the PyMuPDF hard text despite an empty GROBID body


def test_extract_force_ocr_runs_even_on_good_text_layer(db_session, tmp_path, monkeypatch) -> None:
    """#22: force_ocr re-runs OCRmyPDF regardless of quality; summary surfaces provenance."""
    from app.services import ocr as ocr_service

    work, file, pdf, settings = _make_extractable_file(db_session, tmp_path, quality="good")
    monkeypatch.setattr(ocr_service, "ocrmypdf_available", lambda: True)
    ocr_pdf = tmp_path / "paper.ocr.pdf"
    ocr_pdf.write_bytes(b"%PDF-1.4\n%%EOF\n")

    seen = {}

    def fake_maybe_ocr(path, **kw):
        seen["skip_if_good"] = kw.get("skip_if_good")
        return ocr_service.OcrResult(
            ocr_pdf, ran=True, engine="ocrmypdf", text_layer_quality="ocr_added", error=None
        )

    monkeypatch.setattr(ocr_service, "maybe_ocr", fake_maybe_ocr)
    summary = extract_and_store(
        db_session, file=file, fetch_tei=lambda _p: FIXTURE, settings=settings, force_ocr=True
    )
    db_session.commit()
    assert summary["ocr_ran"] is True
    assert summary["ocr_forced"] is True
    assert summary["ocr_available"] is True
    assert seen["skip_if_good"] is False  # forcing disables the good-layer skip
