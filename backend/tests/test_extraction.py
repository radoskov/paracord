"""GROBID TEI parsing and extraction-persistence tests (M2)."""

from pathlib import Path

import pytest
from app.db.base import Base
from app.models.audit import AuditEvent
from app.models.citation import Reference
from app.models.file import File, FileWorkLink, Location
from app.models.metadata import MetadataAssertion
from app.models.source import Source
from app.models.work import Work
from app.services.extraction import extract_and_store, store_parsed_extraction
from app.services.tei_parser import parse_tei
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

FIXTURE = (Path(__file__).parent / "fixtures" / "minimal_grobid_tei.xml").read_text(encoding="utf-8")


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
            Reference.__table__,
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
    first = parsed.references[0]
    assert first.title.startswith("Neural Machine Translation")
    assert first.doi == "10.5555/nmt"
    assert first.year == 2015
    assert parsed.references[1].raw_citation == "Some Unparsed Reference, 2010."


def test_parse_tei_handles_empty_and_invalid() -> None:
    assert parse_tei("").references == []
    assert parse_tei("<not-tei>").title is None


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
    assert db_session.scalar(select(Reference).where(Reference.citing_work_id == work.id)) is not None
    assert len(db_session.scalars(select(Reference)).all()) == 2
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


def test_extract_and_store_uses_fetcher_location_and_audits(db_session, tmp_path: Path) -> None:
    work = Work(canonical_title="f", normalized_title="f", canonical_metadata_source="filename")
    file = File(sha256="a" * 64, size_bytes=10, mime_type="application/pdf")
    db_session.add_all([work, file])
    db_session.flush()
    db_session.add_all(
        [
            FileWorkLink(file_id=file.id, work_id=work.id),
            Location(
                file_id=file.id,
                source_id=None,
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
    assert work.doi == "10.5555/transformer"
    assert db_session.scalars(
        select(AuditEvent).where(AuditEvent.event_type == "extraction.completed")
    ).all()


def test_extract_and_store_requires_server_location(db_session) -> None:
    work = Work(canonical_title="f", normalized_title="f")
    file = File(sha256="b" * 64, size_bytes=10, mime_type="application/pdf")
    db_session.add_all([work, file])
    db_session.flush()
    db_session.add(FileWorkLink(file_id=file.id, work_id=work.id))
    db_session.commit()

    with pytest.raises(ValueError, match="No server-path location"):
        extract_and_store(db_session, file=file, fetch_tei=lambda _p: "")
