"""Scoped citation graph tests (M6)."""

from pathlib import Path

import pytest
from app.db.base import Base
from app.models.citation import Reference
from app.models.organization import Shelf, ShelfWork
from app.models.source import ImportBatch
from app.models.work import Work
from app.services.citation_graph import build_citation_graph
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Heavier suite: slow per-test schema setup (full Base.metadata create_all on file-backed SQLite)
# — moved to the full tier. Run via `make test-full`/`make ready-full` or `pytest -m slow`.
pytestmark = pytest.mark.slow


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
            ImportBatch.__table__,
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


# --- Phase B6: explicit-set + import-batch scopes ---------------------------


def _add_works(db, works: list[Work]) -> None:
    db.add_all(works)
    db.commit()


def test_selected_papers_scope_resolves_passed_ids(db_session) -> None:
    citing = Work(canonical_title="Citing", normalized_title="citing", doi="10.1/citing")
    cited = Work(canonical_title="Cited", normalized_title="cited", doi="10.1/cited")
    outside = Work(canonical_title="Outside", normalized_title="outside", doi="10.1/outside")
    _add_works(db_session, [citing, cited, outside])
    db_session.add(Reference(citing_work_id=citing.id, doi="10.1/cited", title="Cited"))
    db_session.commit()

    graph = build_citation_graph(
        db_session, scope_type="selected_papers", work_ids=[citing.id, cited.id]
    )
    assert {n.id for n in graph.nodes} == {str(citing.id), str(cited.id)}
    assert len(graph.edges) == 1
    # Dropping the cited paper from the selection removes the local edge (citing-only).
    only_citing = build_citation_graph(
        db_session, scope_type="selected_papers", work_ids=[citing.id]
    )
    assert only_citing.edges == []


def test_search_result_scope_resolves_passed_ids(db_session) -> None:
    a = Work(canonical_title="A", normalized_title="a", doi="10.1/a")
    b = Work(canonical_title="B", normalized_title="b", doi="10.1/b")
    _add_works(db_session, [a, b])
    db_session.add(Reference(citing_work_id=a.id, doi="10.1/b", title="B"))
    db_session.commit()

    graph = build_citation_graph(db_session, scope_type="search_result", work_ids=[a.id, b.id])
    assert {n.id for n in graph.nodes} == {str(a.id), str(b.id)}
    assert len(graph.edges) == 1


def test_import_batch_scope_resolves_batch_works(db_session) -> None:
    batch = ImportBatch(input_type="bibtex", status="completed")
    db_session.add(batch)
    db_session.flush()
    citing = Work(
        canonical_title="Citing",
        normalized_title="citing",
        doi="10.1/citing",
        import_batch_id=batch.id,
    )
    cited = Work(
        canonical_title="Cited",
        normalized_title="cited",
        doi="10.1/cited",
        import_batch_id=batch.id,
    )
    other = Work(canonical_title="Other", normalized_title="other", doi="10.1/other")  # NULL batch
    _add_works(db_session, [citing, cited, other])
    db_session.add(Reference(citing_work_id=citing.id, doi="10.1/cited", title="Cited"))
    db_session.commit()

    graph = build_citation_graph(db_session, scope_type="import_batch", scope_id=batch.id)
    assert {n.id for n in graph.nodes} == {str(citing.id), str(cited.id)}
    assert other.id not in {n.work_id for n in graph.nodes}
    assert len(graph.edges) == 1


@pytest.mark.parametrize("scope_type", ["selected_papers", "search_result", "import_batch"])
def test_new_scopes_clamped_to_visible_ids(db_session, scope_type) -> None:
    batch = ImportBatch(input_type="bibtex", status="completed")
    db_session.add(batch)
    db_session.flush()
    citing = Work(
        canonical_title="Citing",
        normalized_title="citing",
        doi="10.1/citing",
        import_batch_id=batch.id,
    )
    hidden = Work(
        canonical_title="Hidden",
        normalized_title="hidden",
        doi="10.1/hidden",
        import_batch_id=batch.id,
    )
    _add_works(db_session, [citing, hidden])
    # citing references the hidden work; even a persisted resolution must never leak it.
    db_session.add(Reference(citing_work_id=citing.id, doi="10.1/hidden", title="Hidden"))
    db_session.commit()

    kwargs = (
        {"scope_id": batch.id}
        if scope_type == "import_batch"
        else {"work_ids": [citing.id, hidden.id]}
    )
    graph = build_citation_graph(
        db_session, scope_type=scope_type, visible_ids={citing.id}, **kwargs
    )
    assert {n.id for n in graph.nodes} == {str(citing.id)}
    assert graph.edges == []  # the hidden target is clamped away


# --- Phase B6: version-collapse ---------------------------------------------


def _version_group(db, rep: Work, member: Work) -> None:
    """Mark ``rep`` as the group representative and ``member`` as part of its group."""
    rep.version_group_id = rep.id
    member.version_group_id = rep.id
    db.commit()


def test_collapse_merges_version_nodes_and_remaps_edges(db_session) -> None:
    rep = Work(canonical_title="A", normalized_title="a", doi="10.1/a")
    alt = Work(canonical_title="A prime", normalized_title="a prime", doi="10.1/aprime")
    citing = Work(canonical_title="B", normalized_title="b", doi="10.1/b")
    _add_works(db_session, [rep, alt, citing])
    _version_group(db_session, rep, alt)
    # B cites the alternate version of A.
    db_session.add(Reference(citing_work_id=citing.id, doi="10.1/aprime", title="A prime"))
    db_session.commit()

    graph = build_citation_graph(
        db_session,
        scope_type="selected_papers",
        work_ids=[rep.id, alt.id, citing.id],
        collapse_versions=True,
    )
    node_ids = {n.id for n in graph.nodes}
    assert str(alt.id) not in node_ids  # non-representative dropped
    assert str(rep.id) in node_ids and str(citing.id) in node_ids
    assert len(graph.edges) == 1
    assert graph.edges[0].source == str(citing.id)
    assert graph.edges[0].target == str(rep.id)  # remapped to representative
    assert graph.summary["collapsed_version_groups"] == 1


def test_collapse_aggregates_and_dedups_parallel_edges(db_session) -> None:
    rep = Work(canonical_title="A", normalized_title="a", doi="10.1/a")
    alt = Work(canonical_title="A prime", normalized_title="a prime", doi="10.1/aprime")
    citing = Work(canonical_title="B", normalized_title="b", doi="10.1/b")
    _add_works(db_session, [rep, alt, citing])
    _version_group(db_session, rep, alt)
    # B cites both A and A' -> after collapse a single B->A edge with summed weight.
    db_session.add_all(
        [
            Reference(citing_work_id=citing.id, doi="10.1/a", title="A"),
            Reference(citing_work_id=citing.id, doi="10.1/aprime", title="A prime"),
        ]
    )
    db_session.commit()

    graph = build_citation_graph(
        db_session,
        scope_type="selected_papers",
        work_ids=[rep.id, alt.id, citing.id],
        collapse_versions=True,
    )
    assert len(graph.edges) == 1
    assert graph.edges[0].weight == 2


def test_collapse_drops_version_to_version_self_loop(db_session) -> None:
    rep = Work(canonical_title="A", normalized_title="a", doi="10.1/a")
    alt = Work(canonical_title="A prime", normalized_title="a prime", doi="10.1/aprime")
    _add_works(db_session, [rep, alt])
    _version_group(db_session, rep, alt)
    # A' cites A (the earlier version) -> both collapse to the representative -> self-loop dropped.
    db_session.add(Reference(citing_work_id=alt.id, doi="10.1/a", title="A"))
    db_session.commit()

    graph = build_citation_graph(
        db_session,
        scope_type="selected_papers",
        work_ids=[rep.id, alt.id],
        collapse_versions=True,
    )
    assert graph.edges == []
    assert {n.id for n in graph.nodes} == {str(rep.id)}


def test_collapse_leaves_external_nodes_untouched(db_session) -> None:
    citing = Work(canonical_title="B", normalized_title="b", doi="10.1/b")
    _add_works(db_session, [citing])
    db_session.add(Reference(citing_work_id=citing.id, doi="10.9/external", title="External paper"))
    db_session.commit()

    graph = build_citation_graph(
        db_session,
        scope_type="selected_papers",
        work_ids=[citing.id],
        node_mode="include_external",
        collapse_versions=True,
    )
    external = [n for n in graph.nodes if n.type == "external"]
    assert len(external) == 1
    assert len(graph.edges) == 1


def test_no_collapse_keeps_version_nodes_separate(db_session) -> None:
    rep = Work(canonical_title="A", normalized_title="a", doi="10.1/a")
    alt = Work(canonical_title="A prime", normalized_title="a prime", doi="10.1/aprime")
    citing = Work(canonical_title="B", normalized_title="b", doi="10.1/b")
    _add_works(db_session, [rep, alt, citing])
    _version_group(db_session, rep, alt)
    db_session.add(Reference(citing_work_id=citing.id, doi="10.1/aprime", title="A prime"))
    db_session.commit()

    graph = build_citation_graph(
        db_session, scope_type="selected_papers", work_ids=[rep.id, alt.id, citing.id]
    )
    assert {str(rep.id), str(alt.id), str(citing.id)} <= {n.id for n in graph.nodes}
    assert graph.edges[0].target == str(alt.id)  # NOT remapped
    assert "collapsed_version_groups" not in graph.summary
