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
from app.models.work import Work
from app.services.extraction import extract_and_store, store_parsed_extraction
from app.services.storage import file_ids_pending_extraction
from app.services.tei_parser import parse_tei
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

FIXTURE = (Path(__file__).parent / "fixtures" / "minimal_grobid_tei.xml").read_text(
    encoding="utf-8"
)


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
    data = GrobidClient("http://grobid:8070", settings=settings)._form_data()
    assert ("consolidateHeader", "0") in data
    assert ("consolidateCitations", "1") in data
    assert [value for key, value in data if key == "teiCoordinates"] == ["ref", "s"]


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
    contexts = get_work_citation_contexts(work.id, db=db_session)
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
