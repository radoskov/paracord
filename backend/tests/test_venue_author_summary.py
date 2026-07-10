"""Venue + author aggregation over a citation-summary scope (batch 10, issue 7)."""

from pathlib import Path

import pytest
from app.db.base import Base
from app.models.citation import Reference
from app.models.metadata import MetadataAssertion
from app.models.organization import Rack, RackShelf, Shelf, ShelfWork
from app.models.source import ImportBatch
from app.models.user import User
from app.models.work import Work
from app.services.citation_summary import SummaryScope
from app.services.venue_author_summary import _author_key, venue_author_summary
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

pytestmark = pytest.mark.slow


@pytest.fixture()
def db_session(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'va.db'}")
    Base.metadata.create_all(
        bind=engine,
        tables=[
            Work.__table__,
            Reference.__table__,
            MetadataAssertion.__table__,
            Shelf.__table__,
            ShelfWork.__table__,
            Rack.__table__,
            RackShelf.__table__,
            ImportBatch.__table__,
            User.__table__,
        ],
    )
    session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    with session_local() as session:
        yield session


def _owner(db) -> User:
    user = User(username="owner", password_hash="x", role="owner")
    db.add(user)
    db.flush()
    return user


def _work(db, title: str, **kwargs) -> Work:
    work = Work(canonical_title=title, normalized_title=title.lower(), **kwargs)
    db.add(work)
    db.flush()
    return work


def _authors(db, work: Work, value: str) -> None:
    db.add(
        MetadataAssertion(
            entity_type="work",
            entity_id=work.id,
            field_name="authors",
            value=value,
            source="grobid",
            selected_as_canonical=True,
        )
    )


def test_author_key_merges_name_forms():
    assert _author_key("Vaswani, A.") == _author_key("Ashish Vaswani") == "vaswani a"
    assert _author_key("Doe, Jane") == "doe j"


def test_venue_and_author_aggregation_with_basic_dedup(db_session):
    owner = _owner(db_session)
    # Two NeurIPS spellings should merge; ICML is its own; w4 has no venue.
    w1 = _work(db_session, "A", venue="NeurIPS", year=2019)
    w2 = _work(db_session, "B", venue="neurips", year=2021)
    w3 = _work(db_session, "C", venue="ICML", year=2020)
    w4 = _work(db_session, "D")
    # "Vaswani, A." and "Ashish Vaswani" should count as one author across two papers.
    _authors(db_session, w1, "Vaswani, A.; Shazeer, N.")
    _authors(db_session, w2, "Ashish Vaswani")
    _authors(db_session, w3, "Jane Doe")
    db_session.flush()

    summary = venue_author_summary(
        db_session,
        owner,
        SummaryScope(type="selected_papers", work_ids=[w1.id, w2.id, w3.id, w4.id]),
    )

    assert summary.scope_work_count == 4
    assert summary.papers_without_venue == 1
    venues = {v.name.lower(): v for v in summary.venues}
    neurips = next(v for v in summary.venues if v.name.lower() == "neurips")
    assert neurips.count == 2
    assert set(neurips.variants) == {"NeurIPS", "neurips"}
    assert neurips.year_min == 2019 and neurips.year_max == 2021
    assert venues["icml"].count == 1
    assert summary.distinct_venue_count == 2

    authors = {a.name: a for a in summary.authors}
    vaswani = next(a for a in summary.authors if a.name in ("Vaswani, A.", "Ashish Vaswani"))
    assert vaswani.count == 2  # merged across w1 + w2
    assert authors["Shazeer, N."].count == 1
    assert summary.papers_without_authors == 1  # w4 has no authors
