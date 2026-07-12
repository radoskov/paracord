"""Scoped citation summary tests (SPEC §8.11, D38 Track C P4)."""

from pathlib import Path

import pytest
from app.db.base import Base
from app.models.citation import Reference, ReferenceCitation
from app.models.organization import Rack, RackShelf, Shelf, ShelfWork
from app.models.source import ImportBatch
from app.models.user import User
from app.models.work import Work
from app.services import citation_summary as cs
from app.services.citation_summary import SummaryScope, citation_summary
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Heavier suite (file-backed SQLite schema setup), aligned with the citation-graph / viz tests.
pytestmark = pytest.mark.slow


@pytest.fixture()
def db_session(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'summary.db'}")
    Base.metadata.create_all(
        bind=engine,
        tables=[
            Work.__table__,
            Reference.__table__,
            ReferenceCitation.__table__,
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
        cs._SUMMARY_CACHE.clear()
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


def _cites(db, make_reference, citing: Work, cited: Work) -> None:
    make_reference(db, citing_work_id=citing.id, doi=cited.doi, title=cited.canonical_title)


def test_most_cited_local_ordering(db_session, make_reference) -> None:
    actor = _owner(db_session)
    hub = _work(db_session, "Hub", doi="10.1/hub")
    minor = _work(db_session, "Minor", doi="10.1/minor")
    a = _work(db_session, "A", doi="10.1/a")
    b = _work(db_session, "B", doi="10.1/b")
    c = _work(db_session, "C", doi="10.1/c")
    # Hub is cited by three works; Minor by one.
    _cites(db_session, make_reference, a, hub)
    _cites(db_session, make_reference, b, hub)
    _cites(db_session, make_reference, c, hub)
    _cites(db_session, make_reference, a, minor)
    db_session.commit()

    summary = citation_summary(db_session, actor, SummaryScope(type="library"))
    ranked = summary.most_cited_local
    assert ranked[0].work_id == hub.id
    assert ranked[0].score == 3.0
    assert ranked[1].work_id == minor.id
    assert ranked[1].score == 1.0


def test_most_cited_external_ranks_by_count_and_excludes_none(db_session) -> None:
    actor = _owner(db_session)
    _work(db_session, "High", citation_count=100)
    _work(db_session, "Mid", citation_count=50)
    _work(db_session, "Uncounted", citation_count=None)
    db_session.commit()

    summary = citation_summary(db_session, actor, SummaryScope(type="library"))
    titles = [w.title for w in summary.most_cited_external]
    assert titles == ["High", "Mid"]
    assert summary.most_cited_external[0].score == 100.0


def test_missing_but_cited_aggregation(db_session, make_reference) -> None:
    actor = _owner(db_session)
    p1 = _work(db_session, "P1", doi="10.1/p1")
    p2 = _work(db_session, "P2", doi="10.1/p2")
    # Two scope works cite the same missing DOI (aggregates to cited_by_count 2); a second missing
    # work is cited once. Neither DOI resolves to any in-library work.
    make_reference(db_session, citing_work_id=p1.id, doi="10.9/missing", title="Missing Popular")
    make_reference(
        db_session, citing_work_id=p2.id, doi="https://doi.org/10.9/MISSING", title="Missing Popular"
    )
    make_reference(db_session, citing_work_id=p1.id, title="Lonely uncollected paper")
    db_session.commit()

    summary = citation_summary(db_session, actor, SummaryScope(type="library"))
    missing = summary.frequently_cited_missing
    assert missing[0].title == "Missing Popular"
    assert missing[0].cited_by_count == 2
    assert missing[0].mention_count == 2
    assert missing[0].doi == "10.9/missing"
    assert missing[0].reference_id is not None
    # The title-only reference aggregates under a normalized-title key.
    assert any(m.title == "Lonely uncollected paper" and m.cited_by_count == 1 for m in missing)


def test_isolated_papers_detected(db_session, make_reference) -> None:
    actor = _owner(db_session)
    citing = _work(db_session, "Citing", doi="10.1/citing")
    cited = _work(db_session, "Cited", doi="10.1/cited")
    island = _work(db_session, "Island", doi="10.1/island")
    _cites(db_session, make_reference, citing, cited)
    db_session.commit()

    summary = citation_summary(db_session, actor, SummaryScope(type="library"))
    isolated_ids = {w.work_id for w in summary.isolated_papers}
    assert island.id in isolated_ids
    assert citing.id not in isolated_ids
    assert cited.id not in isolated_ids


def test_bridge_paper_detected(db_session, make_reference) -> None:
    actor = _owner(db_session)
    # Two triangles joined only through X (C-X-D) -> X carries every cross-cluster shortest path.
    a = _work(db_session, "A", doi="10.1/a")
    b = _work(db_session, "B", doi="10.1/b")
    c = _work(db_session, "C", doi="10.1/c")
    x = _work(db_session, "X", doi="10.1/x")
    d = _work(db_session, "D", doi="10.1/d")
    e = _work(db_session, "E", doi="10.1/e")
    f = _work(db_session, "F", doi="10.1/f")
    for u, v in [(a, b), (b, c), (c, a), (d, e), (e, f), (f, d), (c, x), (x, d)]:
        _cites(db_session, make_reference, u, v)
    db_session.commit()

    summary = citation_summary(db_session, actor, SummaryScope(type="library"))
    assert summary.bridge_method == "brandes_betweenness_undirected"
    assert summary.bridge_papers[0].work_id == x.id
    assert summary.bridge_papers[0].score > 0.0


def test_chronological_distribution(db_session) -> None:
    actor = _owner(db_session)
    _work(db_session, "Y1", year=2020)
    _work(db_session, "Y2", year=2020)
    _work(db_session, "Y3", year=2018)
    _work(db_session, "Unknown", year=None)
    db_session.commit()

    summary = citation_summary(db_session, actor, SummaryScope(type="library"))
    by_year = {y.year: y.work_count for y in summary.chronological}
    assert by_year == {2018: 1, 2020: 2, None: 1}
    # Known years come first, ascending; the unknown-year bucket is last.
    assert [y.year for y in summary.chronological] == [2018, 2020, None]


def test_see_filter_excludes_hidden_work_from_reader(db_session) -> None:
    reader = User(username="reader", password_hash="x", role="reader")
    db_session.add(reader)
    hidden = _work(db_session, "Hidden", year=2015, citation_count=999)
    loose = _work(db_session, "Loose", year=2016, citation_count=5)
    private_shelf = Shelf(name="Private", access_level="private")
    db_session.add(private_shelf)
    db_session.flush()
    db_session.add(ShelfWork(shelf_id=private_shelf.id, work_id=hidden.id))
    db_session.commit()

    summary = citation_summary(db_session, reader, SummaryScope(type="library"))
    external_ids = {w.work_id for w in summary.most_cited_external}
    assert hidden.id not in external_ids
    assert loose.id in external_ids
    assert summary.scope_work_count == 1


def test_cache_hit_serves_without_recompute(db_session, monkeypatch) -> None:
    actor = _owner(db_session)
    _work(db_session, "Solo", doi="10.1/solo", year=2020)
    db_session.commit()

    first = citation_summary(db_session, actor, SummaryScope(type="library"))
    assert len(cs._SUMMARY_CACHE) == 1

    def _boom(*args, **kwargs):
        raise AssertionError("recomputed on a cache hit")

    monkeypatch.setattr(cs, "build_citation_graph", _boom)
    second = citation_summary(db_session, actor, SummaryScope(type="library"))
    assert second.version == first.version
    assert len(cs._SUMMARY_CACHE) == 1


def test_cache_invalidates_when_references_change(db_session, make_reference) -> None:
    actor = _owner(db_session)
    citing = _work(db_session, "Citing", doi="10.1/citing")
    cited = _work(db_session, "Cited", doi="10.1/cited")
    db_session.commit()

    first = citation_summary(db_session, actor, SummaryScope(type="library"))
    # Adding a reference changes the scope's reference count -> a new signature -> recompute.
    _cites(db_session, make_reference, citing, cited)
    db_session.commit()
    second = citation_summary(db_session, actor, SummaryScope(type="library"))
    assert second.version != first.version
    assert second.most_cited_local and second.most_cited_local[0].work_id == cited.id


def test_empty_scope_returns_empty_summary(db_session) -> None:
    actor = _owner(db_session)
    shelf = Shelf(name="Empty")
    db_session.add(shelf)
    db_session.commit()

    summary = citation_summary(db_session, actor, SummaryScope(type="shelf", id=shelf.id))
    assert summary.scope_work_count == 0
    assert summary.most_cited_local == []
    assert summary.notes == ["No papers in this scope."]


# --------------------------------------------------------------------------------------------------
# Endpoint (HTTP/auth) tests — full app against the shared in-memory DB (conftest fixtures).
# --------------------------------------------------------------------------------------------------
def test_endpoint_requires_auth(client) -> None:
    assert client.get("/api/v1/citations/summary").status_code == 401


def test_endpoint_builds_summary(client, db, auth_headers, make_reference) -> None:
    headers = auth_headers("owner")
    citing = Work(canonical_title="Citing", normalized_title="citing", doi="10.1/citing")
    cited = Work(
        canonical_title="Cited", normalized_title="cited", doi="10.1/cited", citation_count=7
    )
    db.add_all([citing, cited])
    db.flush()
    make_reference(db, citing_work_id=citing.id, doi="10.1/cited", title="Cited")
    db.commit()

    response = client.get("/api/v1/citations/summary", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["scope_work_count"] == 2
    assert body["bridge_method"] == "brandes_betweenness_undirected"
    assert body["most_cited_local"][0]["work_id"] == str(cited.id)
    assert body["most_cited_external"][0]["work_id"] == str(cited.id)
    assert body["version"]


def test_endpoint_private_shelf_scope_404_for_reader(client, db, auth_headers) -> None:
    shelf = Shelf(name="Secret", access_level="private")
    db.add(shelf)
    db.commit()
    response = client.get(
        "/api/v1/citations/summary",
        params={"scope_type": "shelf", "scope_id": str(shelf.id)},
        headers=auth_headers("reader"),
    )
    assert response.status_code == 404
