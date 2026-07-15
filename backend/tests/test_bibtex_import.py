"""BibTeX import tests (M3)."""

from pathlib import Path

import pytest
from app.core.security import hash_password
from app.db.base import Base
from app.models.audit import AuditEvent
from app.models.metadata import MetadataAssertion
from app.models.source import ImportBatch
from app.models.user import User
from app.models.work import Work
from app.services.bibtex import import_bibtex, parse_bibtex, parse_bibtex_authors, preview_bibtex
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

SAMPLE = """
@comment{ this should be ignored }
@article{vaswani2017,
  title = {Attention Is All You Need},
  author = {Vaswani, Ashish and Shazeer, Noam},
  journal = {Advances in Neural Information Processing Systems},
  year = {2017},
  doi = {10.5555/3295222.3295349},
}

@inproceedings{he2016,
  title  = "Deep Residual Learning for Image Recognition",
  author = "Kaiming He and Xiangyu Zhang",
  booktitle = {CVPR},
  year   = 2016,
  archiveprefix = {arXiv},
  eprint = {1512.03385},
}
"""


@pytest.fixture()
def db_session(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'bibtex.db'}")
    Base.metadata.create_all(
        bind=engine,
        tables=[
            User.__table__,
            Work.__table__,
            MetadataAssertion.__table__,
            ImportBatch.__table__,
            AuditEvent.__table__,
        ],
    )
    session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    with session_local() as session:
        yield session


@pytest.fixture()
def editor(db_session) -> User:
    user = User(username="editor", password_hash=hash_password("secret"), role="editor")
    db_session.add(user)
    db_session.commit()
    return user


# --- parser -----------------------------------------------------------------


def test_parse_bibtex_reads_entries_and_ignores_comment() -> None:
    entries = parse_bibtex(SAMPLE)
    assert [e.entry_type for e in entries] == ["article", "inproceedings"]
    first = entries[0]
    assert first.key == "vaswani2017"
    assert first.fields["title"] == "Attention Is All You Need"
    assert first.fields["year"] == "2017"
    assert first.fields["doi"] == "10.5555/3295222.3295349"


def test_parse_bibtex_handles_quoted_and_bare_values() -> None:
    entry = parse_bibtex(SAMPLE)[1]
    assert entry.fields["title"] == "Deep Residual Learning for Image Recognition"
    assert entry.fields["year"] == "2016"  # bare number
    assert entry.fields["eprint"] == "1512.03385"


def test_parse_bibtex_authors_normalizes_last_first() -> None:
    assert parse_bibtex_authors("Vaswani, Ashish and Shazeer, Noam") == [
        "Ashish Vaswani",
        "Noam Shazeer",
    ]
    assert parse_bibtex_authors("Kaiming He and Xiangyu Zhang") == ["Kaiming He", "Xiangyu Zhang"]


def test_parse_bibtex_handles_nested_braces() -> None:
    entry = parse_bibtex("@article{k, title = {A {Nested} Title}, year={2020}}")[0]
    assert entry.fields["title"] == "A Nested Title"


# --- import_bibtex ----------------------------------------------------------


def test_import_bibtex_creates_works_with_provenance(db_session, editor: User) -> None:
    batch = import_bibtex(db_session, SAMPLE, actor=editor)
    db_session.commit()

    assert batch.input_type == "bibtex"
    assert batch.stats["created"] == 2
    works = {w.canonical_title: w for w in db_session.scalars(select(Work)).all()}
    assert set(works) == {
        "Attention Is All You Need",
        "Deep Residual Learning for Image Recognition",
    }
    resnet = works["Deep Residual Learning for Image Recognition"]
    assert resnet.year == 2016
    assert resnet.arxiv_id == "1512.03385"  # from archiveprefix=arXiv + eprint
    assert resnet.venue == "CVPR"
    assert resnet.work_type == "inproceedings"

    authors = db_session.scalar(
        select(MetadataAssertion.value).where(
            MetadataAssertion.field_name == "authors", MetadataAssertion.source == "bibtex"
        )
    )
    assert "Ashish Vaswani" in authors
    assert db_session.scalar(select(AuditEvent).where(AuditEvent.event_type == "import.bibtex"))


def test_import_bibtex_dedupes_on_reimport(db_session, editor: User) -> None:
    import_bibtex(db_session, SAMPLE, actor=editor)
    db_session.commit()
    batch = import_bibtex(db_session, SAMPLE, actor=editor)  # same content again
    db_session.commit()

    assert batch.stats["created"] == 0
    assert batch.stats["matched"] == 2
    assert db_session.scalar(select(func.count()).select_from(Work)) == 2


def test_import_bibtex_skips_entries_without_title(db_session, editor: User) -> None:
    batch = import_bibtex(db_session, "@misc{x, author={Nobody}, year={1999}}", actor=editor)
    db_session.commit()
    assert batch.stats["created"] == 0
    assert batch.stats["skipped"] == 1


def test_preview_bibtex_maps_entries_to_drafts_without_writing(db_session) -> None:
    drafts = preview_bibtex(db_session, SAMPLE)

    assert [d.suggested_title for d in drafts] == [
        "Attention Is All You Need",
        "Deep Residual Learning for Image Recognition",
    ]
    first, second = drafts
    assert first.engine == "bibtex"
    assert first.match_status == "matched"
    assert first.suggested_doi == "10.5555/3295222.3295349"
    assert first.suggested_authors == ["Ashish Vaswani", "Noam Shazeer"]
    assert first.existing_work_id is None
    assert second.suggested_arxiv_id == "1512.03385"
    assert second.suggested_work_type == "inproceedings"
    assert second.suggested_venue == "CVPR"
    # Preview never writes.
    assert db_session.scalar(select(func.count()).select_from(Work)) == 0


def test_preview_bibtex_flags_entries_already_in_library(db_session, editor: User) -> None:
    import_bibtex(db_session, SAMPLE, actor=editor)
    db_session.commit()

    drafts = preview_bibtex(db_session, SAMPLE)
    assert all(d.existing_work_id is not None for d in drafts)


# --- API --------------------------------------------------------------------


def test_bibtex_import_api_rejects_empty(client, auth_headers) -> None:
    r = client.post(
        "/api/v1/imports/bibtex", headers=auth_headers("editor"), json={"content": "   "}
    )
    assert r.status_code == 400


def test_bibtex_import_api_requires_editor(client, auth_headers) -> None:
    r = client.post(
        "/api/v1/imports/bibtex",
        headers=auth_headers("reader"),
        json={"content": "@article{k, title={X}}"},
    )
    assert r.status_code == 403
