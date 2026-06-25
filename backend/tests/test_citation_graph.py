"""Scoped citation graph tests (M6)."""

from pathlib import Path

import pytest
from app.db.base import Base
from app.models.citation import Reference
from app.models.organization import Shelf, ShelfWork
from app.models.work import Work
from app.services.citation_graph import build_citation_graph
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture()
def db_session(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'graph.db'}")
    Base.metadata.create_all(
        bind=engine,
        tables=[
            Work.__table__,
            Reference.__table__,
            Shelf.__table__,
            ShelfWork.__table__,
        ],
    )
    session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    with session_local() as session:
        yield session


def _shelf_with_works(db, works: list[Work]) -> Shelf:
    shelf = Shelf(name="Scope")
    db.add(shelf)
    db.add_all(works)
    db.flush()
    db.add_all(ShelfWork(shelf_id=shelf.id, work_id=work.id) for work in works)
    db.commit()
    return shelf


def test_local_match_edge_between_scope_works(db_session) -> None:
    citing = Work(canonical_title="Citing", normalized_title="citing", doi="10.1/citing")
    cited = Work(canonical_title="Cited", normalized_title="cited", doi="10.1/cited")
    shelf = _shelf_with_works(db_session, [citing, cited])
    # Citing's bibliography references the cited work by DOI (twice -> weight 2).
    db_session.add_all(
        [
            Reference(citing_work_id=citing.id, doi="10.1/CITED", title="Cited"),
            Reference(citing_work_id=citing.id, doi="https://doi.org/10.1/cited", title="Cited"),
        ]
    )
    db_session.commit()

    graph = build_citation_graph(db_session, scope_type="shelf", scope_id=shelf.id)

    assert {node.id for node in graph.nodes} == {str(citing.id), str(cited.id)}
    assert len(graph.edges) == 1
    edge = graph.edges[0]
    assert edge.source == str(citing.id)
    assert edge.target == str(cited.id)
    assert edge.weight == 2
    assert edge.resolution == "local_match"
    assert graph.summary["edge_count"] == 1
    assert graph.summary["external_node_count"] == 0


def test_local_only_drops_external_references(db_session) -> None:
    citing = Work(canonical_title="Citing", normalized_title="citing")
    shelf = _shelf_with_works(db_session, [citing])
    db_session.add(
        Reference(citing_work_id=citing.id, title="Some uncollected paper", doi="10.9/elsewhere")
    )
    db_session.commit()

    local = build_citation_graph(
        db_session, scope_type="shelf", scope_id=shelf.id, node_mode="local_only"
    )
    assert local.edges == []
    assert local.summary["unresolved_reference_count"] == 0
    assert local.summary["external_node_count"] == 0

    full = build_citation_graph(
        db_session, scope_type="shelf", scope_id=shelf.id, node_mode="include_external"
    )
    assert len(full.edges) == 1
    assert full.edges[0].resolution == "external"
    assert full.summary["external_node_count"] == 1
    external = next(node for node in full.nodes if node.type == "external")
    assert external.label == "Some uncollected paper"


def test_self_citation_is_dropped(db_session) -> None:
    work = Work(canonical_title="Solo", normalized_title="solo", doi="10.1/solo")
    shelf = _shelf_with_works(db_session, [work])
    db_session.add(Reference(citing_work_id=work.id, doi="10.1/solo", title="Solo"))
    db_session.commit()

    graph = build_citation_graph(db_session, scope_type="shelf", scope_id=shelf.id)
    assert graph.edges == []


def test_empty_scope_returns_empty_graph(db_session) -> None:
    shelf = _shelf_with_works(db_session, [])
    graph = build_citation_graph(db_session, scope_type="shelf", scope_id=shelf.id)
    assert graph.nodes == []
    assert graph.edges == []
    assert graph.summary["scope_work_count"] == 0
