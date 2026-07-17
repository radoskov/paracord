"""Scoped citation graph tests (M6)."""

from pathlib import Path

import pytest
from app.db.base import Base
from app.models.citation import Reference, ReferenceCitation
from app.models.duplicate import DuplicateCandidate
from app.models.external_citation import ExternalCitationLink, ExternalPaper
from app.models.file import File, FileWorkLink
from app.models.organization import Rack, RackShelf, Shelf, ShelfWork, Tag, TagLink
from app.models.source import ImportBatch
from app.models.work import Work
from app.services.citation_graph import (
    build_citation_graph,
    build_citation_neighborhood,
)
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
            ReferenceCitation.__table__,
            Shelf.__table__,
            ShelfWork.__table__,
            Rack.__table__,
            RackShelf.__table__,
            ImportBatch.__table__,
            Tag.__table__,
            TagLink.__table__,
            File.__table__,
            FileWorkLink.__table__,
            DuplicateCandidate.__table__,
            ExternalPaper.__table__,
            ExternalCitationLink.__table__,
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


def test_local_match_edge_between_scope_works(db_session, make_reference) -> None:
    citing = Work(canonical_title="Citing", normalized_title="citing", doi="10.1/citing")
    cited = Work(canonical_title="Cited", normalized_title="cited", doi="10.1/cited")
    shelf = _shelf_with_works(db_session, [citing, cited])
    # Citing's bibliography references the cited work by DOI (twice -> weight 2).
    make_reference(db_session, citing_work_id=citing.id, doi="10.1/CITED", title="Cited")
    make_reference(
        db_session, citing_work_id=citing.id, doi="https://doi.org/10.1/cited", title="Cited"
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


def test_citing_papers_add_typed_edges_into_scope(db_session) -> None:
    """2026-07-16: fetched incoming citations appear as external 'citing' nodes with edges pointing
    INTO the scope work (relation='citing'); local_only hides the external citer."""
    cited = Work(canonical_title="Cited", normalized_title="cited", doi="10.1/cited")
    shelf = _shelf_with_works(db_session, [cited])
    ext = ExternalPaper(dedup_key="10.1/citer", source="openalex", doi="10.1/citer", title="Citer")
    db_session.add(ext)
    db_session.flush()
    db_session.add(ExternalCitationLink(external_paper_id=ext.id, work_id=cited.id))
    db_session.commit()

    g = build_citation_graph(
        db_session,
        scope_type="shelf",
        scope_id=shelf.id,
        node_mode="include_external",
        include_citing=True,
    )
    citing_edges = [e for e in g.edges if e.relation == "citing"]
    assert len(citing_edges) == 1
    assert citing_edges[0].target == str(cited.id)  # edge points INTO the scope work
    assert g.summary["citing_available"] is True
    assert any(n.id.startswith("citing:") and n.type == "external" for n in g.nodes)

    # local_only drops the external citer entirely.
    g2 = build_citation_graph(
        db_session,
        scope_type="shelf",
        scope_id=shelf.id,
        node_mode="local_only",
        include_citing=True,
    )
    assert not [e for e in g2.edges if e.relation == "citing"]


def test_separate_caps_for_references_and_citing(db_session, make_reference) -> None:
    """References and citing papers have INDEPENDENT budgets — a tiny citing cap doesn't shrink the
    reference set, and vice versa."""
    work = Work(canonical_title="W", normalized_title="w", doi="10.1/w")
    shelf = _shelf_with_works(db_session, [work])
    for i in range(5):  # 5 external references
        make_reference(db_session, citing_work_id=work.id, doi=f"10.9/ref{i}", title=f"Ref {i}")
    for i in range(5):  # 5 external citing papers
        ext = ExternalPaper(
            dedup_key=f"10.8/cit{i}", source="openalex", doi=f"10.8/cit{i}", title=f"Cit {i}"
        )
        db_session.add(ext)
        db_session.flush()
        db_session.add(ExternalCitationLink(external_paper_id=ext.id, work_id=work.id))
    db_session.commit()

    g = build_citation_graph(
        db_session,
        scope_type="shelf",
        scope_id=shelf.id,
        node_mode="include_external",
        max_external=5,
        max_external_citing=1,
        include_citing=True,
    )
    ref_ext = [n for n in g.nodes if n.type == "external" and n.id.startswith("ext:")]
    cit_ext = [n for n in g.nodes if n.type == "external" and n.id.startswith("citing:")]
    assert len(ref_ext) == 5  # references keep their full budget
    assert len(cit_ext) == 1  # citing capped independently
    assert g.summary["citing_hidden"] == 4


def test_local_only_drops_external_references(db_session, make_reference) -> None:
    citing = Work(canonical_title="Citing", normalized_title="citing")
    shelf = _shelf_with_works(db_session, [citing])
    make_reference(
        db_session, citing_work_id=citing.id, title="Some uncollected paper", doi="10.9/elsewhere"
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


def test_self_citation_is_dropped(db_session, make_reference) -> None:
    work = Work(canonical_title="Solo", normalized_title="solo", doi="10.1/solo")
    shelf = _shelf_with_works(db_session, [work])
    make_reference(db_session, citing_work_id=work.id, doi="10.1/solo", title="Solo")
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


def test_selected_papers_scope_resolves_passed_ids(db_session, make_reference) -> None:
    citing = Work(canonical_title="Citing", normalized_title="citing", doi="10.1/citing")
    cited = Work(canonical_title="Cited", normalized_title="cited", doi="10.1/cited")
    outside = Work(canonical_title="Outside", normalized_title="outside", doi="10.1/outside")
    _add_works(db_session, [citing, cited, outside])
    make_reference(db_session, citing_work_id=citing.id, doi="10.1/cited", title="Cited")
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


def test_search_result_scope_resolves_passed_ids(db_session, make_reference) -> None:
    a = Work(canonical_title="A", normalized_title="a", doi="10.1/a")
    b = Work(canonical_title="B", normalized_title="b", doi="10.1/b")
    _add_works(db_session, [a, b])
    make_reference(db_session, citing_work_id=a.id, doi="10.1/b", title="B")
    db_session.commit()

    graph = build_citation_graph(db_session, scope_type="search_result", work_ids=[a.id, b.id])
    assert {n.id for n in graph.nodes} == {str(a.id), str(b.id)}
    assert len(graph.edges) == 1


def test_import_batch_scope_resolves_batch_works(db_session, make_reference) -> None:
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
    make_reference(db_session, citing_work_id=citing.id, doi="10.1/cited", title="Cited")
    db_session.commit()

    graph = build_citation_graph(db_session, scope_type="import_batch", scope_id=batch.id)
    assert {n.id for n in graph.nodes} == {str(citing.id), str(cited.id)}
    assert other.id not in {n.work_id for n in graph.nodes}
    assert len(graph.edges) == 1


@pytest.mark.parametrize("scope_type", ["selected_papers", "search_result", "import_batch"])
def test_new_scopes_clamped_to_visible_ids(db_session, scope_type, make_reference) -> None:
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
    make_reference(db_session, citing_work_id=citing.id, doi="10.1/hidden", title="Hidden")
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


def test_collapse_merges_version_nodes_and_remaps_edges(db_session, make_reference) -> None:
    rep = Work(canonical_title="A", normalized_title="a", doi="10.1/a")
    alt = Work(canonical_title="A prime", normalized_title="a prime", doi="10.1/aprime")
    citing = Work(canonical_title="B", normalized_title="b", doi="10.1/b")
    _add_works(db_session, [rep, alt, citing])
    _version_group(db_session, rep, alt)
    # B cites the alternate version of A.
    make_reference(db_session, citing_work_id=citing.id, doi="10.1/aprime", title="A prime")
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


def test_collapse_aggregates_and_dedups_parallel_edges(db_session, make_reference) -> None:
    rep = Work(canonical_title="A", normalized_title="a", doi="10.1/a")
    alt = Work(canonical_title="A prime", normalized_title="a prime", doi="10.1/aprime")
    citing = Work(canonical_title="B", normalized_title="b", doi="10.1/b")
    _add_works(db_session, [rep, alt, citing])
    _version_group(db_session, rep, alt)
    # B cites both A and A' -> after collapse a single B->A edge with summed weight.
    make_reference(db_session, citing_work_id=citing.id, doi="10.1/a", title="A")
    make_reference(db_session, citing_work_id=citing.id, doi="10.1/aprime", title="A prime")
    db_session.commit()

    graph = build_citation_graph(
        db_session,
        scope_type="selected_papers",
        work_ids=[rep.id, alt.id, citing.id],
        collapse_versions=True,
    )
    assert len(graph.edges) == 1
    assert graph.edges[0].weight == 2


def test_collapse_drops_version_to_version_self_loop(db_session, make_reference) -> None:
    rep = Work(canonical_title="A", normalized_title="a", doi="10.1/a")
    alt = Work(canonical_title="A prime", normalized_title="a prime", doi="10.1/aprime")
    _add_works(db_session, [rep, alt])
    _version_group(db_session, rep, alt)
    # A' cites A (the earlier version) -> both collapse to the representative -> self-loop dropped.
    make_reference(db_session, citing_work_id=alt.id, doi="10.1/a", title="A")
    db_session.commit()

    graph = build_citation_graph(
        db_session,
        scope_type="selected_papers",
        work_ids=[rep.id, alt.id],
        collapse_versions=True,
    )
    assert graph.edges == []
    assert {n.id for n in graph.nodes} == {str(rep.id)}


def test_collapse_leaves_external_nodes_untouched(db_session, make_reference) -> None:
    citing = Work(canonical_title="B", normalized_title="b", doi="10.1/b")
    _add_works(db_session, [citing])
    make_reference(
        db_session, citing_work_id=citing.id, doi="10.9/external", title="External paper"
    )
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


def test_no_collapse_keeps_version_nodes_separate(db_session, make_reference) -> None:
    rep = Work(canonical_title="A", normalized_title="a", doi="10.1/a")
    alt = Work(canonical_title="A prime", normalized_title="a prime", doi="10.1/aprime")
    citing = Work(canonical_title="B", normalized_title="b", doi="10.1/b")
    _add_works(db_session, [rep, alt, citing])
    _version_group(db_session, rep, alt)
    make_reference(db_session, citing_work_id=citing.id, doi="10.1/aprime", title="A prime")
    db_session.commit()

    graph = build_citation_graph(
        db_session, scope_type="selected_papers", work_ids=[rep.id, alt.id, citing.id]
    )
    assert {str(rep.id), str(alt.id), str(citing.id)} <= {n.id for n in graph.nodes}
    assert graph.edges[0].target == str(alt.id)  # NOT remapped
    assert "collapsed_version_groups" not in graph.summary


# ── §8.9 depth: centrality sizing, color-by, warnings, neighborhood (Track C P5b) ────────────────


def _hub_and_spokes(db, make_reference) -> tuple[Work, list[Work]]:
    """A hub cited by three spokes: hub has the highest in-centrality on the directed graph."""
    hub = Work(canonical_title="Hub", normalized_title="hub", doi="10.1/hub")
    spokes = [
        Work(canonical_title=f"S{i}", normalized_title=f"s{i}", doi=f"10.1/s{i}") for i in range(3)
    ]
    _add_works(db, [hub, *spokes])
    for s in spokes:
        make_reference(db, citing_work_id=s.id, doi="10.1/hub", title="Hub")
    db.commit()
    return hub, spokes


def test_compute_metrics_off_by_default_leaves_zero_centrality(db_session, make_reference) -> None:
    hub, spokes = _hub_and_spokes(db_session, make_reference)
    graph = build_citation_graph(
        db_session, scope_type="selected_papers", work_ids=[hub.id, *[s.id for s in spokes]]
    )
    assert all(node.pagerank == 0.0 and node.betweenness == 0.0 for node in graph.nodes)


def test_centrality_ranks_hub_above_spokes(db_session, make_reference) -> None:
    hub, spokes = _hub_and_spokes(db_session, make_reference)
    graph = build_citation_graph(
        db_session,
        scope_type="selected_papers",
        work_ids=[hub.id, *[s.id for s in spokes]],
        compute_metrics=True,
    )
    by_id = {node.id: node for node in graph.nodes}
    hub_node = by_id[str(hub.id)]
    spoke_nodes = [by_id[str(s.id)] for s in spokes]
    # The hub is the sink of every citation edge → higher PageRank and degree than any spoke.
    assert all(hub_node.pagerank > s.pagerank for s in spoke_nodes)
    assert all(hub_node.degree >= s.degree for s in spoke_nodes)
    # PageRank forms a distribution summing to ~1 over the nodes.
    assert abs(sum(node.pagerank for node in graph.nodes) - 1.0) < 1.0e-3


def test_betweenness_flags_the_bridge(db_session, make_reference) -> None:
    # A - bridge - B (path): the bridge lies on the only shortest path, so it alone scores > 0.
    left = Work(canonical_title="L", normalized_title="l", doi="10.1/l")
    bridge = Work(canonical_title="Br", normalized_title="br", doi="10.1/br")
    right = Work(canonical_title="R", normalized_title="r", doi="10.1/r")
    _add_works(db_session, [left, bridge, right])
    make_reference(db_session, citing_work_id=left.id, doi="10.1/br", title="Br")
    make_reference(db_session, citing_work_id=bridge.id, doi="10.1/r", title="R")
    db_session.commit()
    graph = build_citation_graph(
        db_session,
        scope_type="selected_papers",
        work_ids=[left.id, bridge.id, right.id],
        compute_metrics=True,
    )
    by_id = {node.id: node for node in graph.nodes}
    assert by_id[str(bridge.id)].betweenness > 0.0
    assert by_id[str(left.id)].betweenness == 0.0
    assert by_id[str(right.id)].betweenness == 0.0


def test_edge_weight_is_mention_count(db_session, make_reference) -> None:
    citing = Work(canonical_title="C", normalized_title="c", doi="10.1/c")
    cited = Work(canonical_title="D", normalized_title="d", doi="10.1/d")
    _add_works(db_session, [citing, cited])
    make_reference(db_session, citing_work_id=citing.id, doi="10.1/d", title="D")
    make_reference(db_session, citing_work_id=citing.id, doi="10.1/d", title="D")
    make_reference(db_session, citing_work_id=citing.id, doi="10.1/d", title="D")
    db_session.commit()
    graph = build_citation_graph(
        db_session,
        scope_type="selected_papers",
        work_ids=[citing.id, cited.id],
        compute_metrics=True,
    )
    assert graph.edges[0].weight == 3


def test_color_by_status_and_topic(db_session) -> None:
    read = Work(canonical_title="A", normalized_title="a", reading_status="read", topics=["nlp"])
    unread = Work(canonical_title="B", normalized_title="b")  # defaults: unread / no topics
    _add_works(db_session, [read, unread])
    db_session.commit()

    by_status = build_citation_graph(
        db_session,
        scope_type="selected_papers",
        work_ids=[read.id, unread.id],
        compute_metrics=True,
        color_by="status",
    )
    groups = {n.work_id: n.color_group for n in by_status.nodes}
    assert groups[read.id] == "read"
    assert groups[unread.id] == "unread"
    assert by_status.summary["color_by"] == "status"

    by_topic = build_citation_graph(
        db_session,
        scope_type="selected_papers",
        work_ids=[read.id, unread.id],
        compute_metrics=True,
        color_by="topic",
    )
    topic_groups = {n.work_id: n.color_group for n in by_topic.nodes}
    assert topic_groups[read.id] == "nlp"
    assert topic_groups[unread.id] == "untopiced"


def test_color_by_year_defaults_unknown(db_session) -> None:
    dated = Work(canonical_title="A", normalized_title="a", year=2021)
    undated = Work(canonical_title="B", normalized_title="b")
    _add_works(db_session, [dated, undated])
    db_session.commit()

    by_year = build_citation_graph(
        db_session,
        scope_type="selected_papers",
        work_ids=[dated.id, undated.id],
        compute_metrics=True,
        color_by="year",
    )
    groups = {n.work_id: n.color_group for n in by_year.nodes}
    assert groups[dated.id] == "2021"
    assert groups[undated.id] == "unknown"
    assert by_year.summary["color_by"] == "year"


def test_color_by_shelf_skips_private_shelf(db_session) -> None:
    work = Work(canonical_title="P", normalized_title="p")
    db_session.add(work)
    public = Shelf(name="Public", access_level="open")
    private = Shelf(name="Secret", access_level="private")
    db_session.add_all([public, private])
    db_session.flush()
    db_session.add_all(
        [
            ShelfWork(shelf_id=public.id, work_id=work.id),
            ShelfWork(shelf_id=private.id, work_id=work.id),
        ]
    )
    db_session.commit()
    graph = build_citation_graph(
        db_session,
        scope_type="selected_papers",
        work_ids=[work.id],
        compute_metrics=True,
        color_by="shelf",
    )
    # The private shelf's name is never surfaced as a color; the public one is used.
    assert graph.nodes[0].color_group == "Public"


def test_color_by_tag_defaults_untagged(db_session) -> None:
    tagged = Work(canonical_title="T", normalized_title="t")
    plain = Work(canonical_title="U", normalized_title="u")
    db_session.add_all([tagged, plain])
    tag = Tag(name="method", normalized_name="method")
    db_session.add(tag)
    db_session.flush()
    db_session.add(TagLink(tag_id=tag.id, entity_type="work", entity_id=tagged.id))
    db_session.commit()
    graph = build_citation_graph(
        db_session,
        scope_type="selected_papers",
        work_ids=[tagged.id, plain.id],
        compute_metrics=True,
        color_by="tag",
    )
    groups = {n.work_id: n.color_group for n in graph.nodes}
    assert groups[tagged.id] == "method"
    assert groups[plain.id] == "untagged"


def test_warning_badge_from_file_link_and_duplicate(db_session) -> None:
    warned_file = Work(canonical_title="W", normalized_title="w")
    dup_a = Work(canonical_title="X", normalized_title="x")
    dup_b = Work(canonical_title="Y", normalized_title="y")
    clean = Work(canonical_title="Z", normalized_title="z")
    _add_works(db_session, [warned_file, dup_a, dup_b, clean])
    a_file = File(original_filename="a.pdf", sha256="h" * 64, size_bytes=1)
    db_session.add(a_file)
    db_session.flush()
    db_session.add(
        FileWorkLink(
            file_id=a_file.id, work_id=warned_file.id, warning_state="work_has_multiple_files"
        )
    )
    db_session.add(
        DuplicateCandidate(
            candidate_type="work",
            entity_a_type="work",
            entity_a_id=dup_a.id,
            entity_b_type="work",
            entity_b_id=dup_b.id,
            score=0.9,
            status="open",
        )
    )
    db_session.commit()
    graph = build_citation_graph(
        db_session,
        scope_type="selected_papers",
        work_ids=[warned_file.id, dup_a.id, dup_b.id, clean.id],
        compute_metrics=True,
    )
    warned = {n.work_id for n in graph.nodes if n.warning}
    # File-link warning flags one work; the open-duplicate flags BOTH sides; the control stays clean.
    assert warned == {warned_file.id, dup_a.id, dup_b.id}


def test_neighborhood_returns_one_hop_set(db_session, make_reference) -> None:
    focus = Work(canonical_title="Focus", normalized_title="focus", doi="10.1/focus")
    cites = Work(canonical_title="Cited", normalized_title="cited", doi="10.1/cited")
    citer = Work(canonical_title="Citer", normalized_title="citer", doi="10.1/citer")
    far = Work(canonical_title="Far", normalized_title="far", doi="10.1/far")
    _add_works(db_session, [focus, cites, citer, far])
    make_reference(
        db_session, citing_work_id=focus.id, doi="10.1/cited", title="Cited"
    )  # focus -> cites
    make_reference(
        db_session, citing_work_id=citer.id, doi="10.1/focus", title="Focus"
    )  # citer -> focus
    make_reference(
        db_session, citing_work_id=far.id, doi="10.1/citer", title="Citer"
    )  # far -> citer (2 hop)
    db_session.commit()
    graph = build_citation_neighborhood(db_session, work_id=focus.id, hops=1)
    ids = {n.work_id for n in graph.nodes}
    assert ids == {focus.id, cites.id, citer.id}  # `far` is two hops away → excluded
    assert graph.summary["focus_work_id"] == str(focus.id)
    assert graph.summary["hops"] == 1


def test_neighborhood_none_for_hidden_focus(db_session) -> None:
    focus = Work(canonical_title="Focus", normalized_title="focus")
    db_session.add(focus)
    db_session.commit()
    assert build_citation_neighborhood(db_session, work_id=focus.id, visible_ids=set()) is None


# ── Endpoint: §8.9 depth params + neighborhood (uses the in-memory `client` fixture) ─────────────


def test_endpoint_citation_graph_ships_depth_fields(
    client, auth_headers, db, make_reference
) -> None:
    citing = Work(canonical_title="Citing", normalized_title="citing", doi="10.1/c")
    cited = Work(
        canonical_title="Cited", normalized_title="cited", doi="10.1/d", reading_status="read"
    )
    db.add_all([citing, cited])
    db.flush()
    make_reference(db, citing_work_id=citing.id, doi="10.1/d", title="Cited")
    db.commit()

    response = client.post(
        "/api/v1/graphs/citation",
        headers=auth_headers("owner"),
        json={"scope": {"type": "library"}, "color_by": "status"},
    )
    assert response.status_code == 200
    body = response.json()
    node = next(n for n in body["nodes"] if n["work_id"] == str(cited.id))
    # Depth encodings present and populated server-side.
    assert node["pagerank"] > 0.0
    assert node["degree"] >= 1
    assert node["color_group"] == "read"
    assert node["warning"] is False
    assert body["summary"]["color_by"] == "status"


def test_endpoint_neighborhood_returns_one_hop_and_requires_auth(
    client, auth_headers, db, make_reference
) -> None:
    focus = Work(canonical_title="Focus", normalized_title="focus", doi="10.1/focus")
    cited = Work(canonical_title="Cited", normalized_title="cited", doi="10.1/cited")
    db.add_all([focus, cited])
    db.flush()
    make_reference(db, citing_work_id=focus.id, doi="10.1/cited", title="Cited")
    db.commit()

    # Unauthenticated → 401 (auth dependency on the works router).
    assert client.get(f"/api/v1/works/{focus.id}/citation-neighborhood").status_code == 401

    response = client.get(
        f"/api/v1/works/{focus.id}/citation-neighborhood", headers=auth_headers("owner")
    )
    assert response.status_code == 200
    body = response.json()
    assert {n["work_id"] for n in body["nodes"]} == {str(focus.id), str(cited.id)}
    assert body["summary"]["focus_work_id"] == str(focus.id)
    assert body["summary"]["hops"] == 1

    # A missing focus work → 404.
    missing = client.get(
        f"/api/v1/works/{focus.id}0000/citation-neighborhood", headers=auth_headers("owner")
    )
    assert missing.status_code in (404, 422)


def test_endpoint_neighborhood_enforces_reader_see_filter(
    client, auth_headers, db, make_reference
) -> None:
    """A reader gets 404 for a focus paper they cannot SEE, and never sees a hidden neighbor.

    Endpoint-level companion to the service tests: the works-router auth is exercised through the
    real reader/owner sessions, and the neighborhood payload is clamped to the reader's visible set.
    """
    private = Shelf(name="Private", access_level="private")
    db.add(private)
    db.flush()

    # A focus paper that lives only on a private shelf → invisible to a reader.
    hidden_focus = Work(canonical_title="HFocus", normalized_title="hfocus", doi="10.1/hf")
    hidden_cited = Work(canonical_title="HCited", normalized_title="hcited", doi="10.1/hc")
    db.add_all([hidden_focus, hidden_cited])
    db.flush()
    db.add_all(
        [
            ShelfWork(shelf_id=private.id, work_id=hidden_focus.id),
            ShelfWork(shelf_id=private.id, work_id=hidden_cited.id),
        ]
    )
    make_reference(db, citing_work_id=hidden_focus.id, doi="10.1/hc", title="HCited")

    # A loose (visible) focus that cites the hidden paper by DOI.
    focus = Work(canonical_title="Focus2", normalized_title="focus2", doi="10.1/f2")
    db.add(focus)
    db.flush()
    make_reference(db, citing_work_id=focus.id, doi="10.1/hc", title="HCited")
    db.commit()

    # The reader cannot SEE the private focus → 404.
    reader = auth_headers("reader")
    assert (
        client.get(
            f"/api/v1/works/{hidden_focus.id}/citation-neighborhood", headers=reader
        ).status_code
        == 404
    )

    # The reader SEES the loose focus, but the hidden neighbor is clamped out of the payload.
    resp = client.get(f"/api/v1/works/{focus.id}/citation-neighborhood", headers=reader)
    assert resp.status_code == 200
    reader_ids = {n["work_id"] for n in resp.json()["nodes"]}
    assert str(focus.id) in reader_ids
    assert str(hidden_cited.id) not in reader_ids

    # The owner, by contrast, resolves the hidden neighbor through the shared reference.
    owner_resp = client.get(
        f"/api/v1/works/{focus.id}/citation-neighborhood", headers=auth_headers("owner")
    )
    assert owner_resp.status_code == 200
    assert str(hidden_cited.id) in {n["work_id"] for n in owner_resp.json()["nodes"]}


def test_distribute_external_keep_is_even_not_first_come() -> None:
    """2026-07-16: the external budget is spread across scope papers — a paper with many refs must
    not starve a paper with few."""
    from app.services.citation_graph import _distribute_external_keep

    ext = {
        "A": [(f"a{i}", 1) for i in range(10)],  # 10 external refs
        "B": [("b0", 1), ("b1", 1)],  # only 2
    }
    keep = _distribute_external_keep(ext, 6)
    assert "b0" in keep and "b1" in keep  # the small paper is not starved
    assert sum(1 for k in keep if k.startswith("a")) <= 5  # the big paper doesn't eat the budget
    assert len(keep) <= 6


def test_distribute_external_keep_exhausts_budget_when_limit_below_paper_count() -> None:
    """When limit < N, the absolute pass exhausts the budget and later papers get none (no relative
    pass)."""
    from app.services.citation_graph import _distribute_external_keep

    ext = {"A": [("a0", 1)], "B": [("b0", 1)], "C": [("c0", 1)]}
    keep = _distribute_external_keep(ext, 1)
    assert len(keep) == 1  # only one paper's ref survives


def test_citation_graph_node_cap_keeps_best_connected(db, make_user) -> None:
    """L-a: above max_nodes, the highest-degree nodes survive and the hidden count is reported."""
    from app.models.citation import Reference, ReferenceCitation
    from app.models.work import Work
    from app.services.citation_graph import build_citation_graph
    from app.utils.normalization import normalize_title

    hub = Work(canonical_title="Hub", normalized_title="hub", doi="10.1/hub")
    db.add(hub)
    db.flush()
    citers = []
    for i in range(4):
        w = Work(canonical_title=f"Citer {i}", normalized_title=normalize_title(f"Citer {i}"))
        db.add(w)
        db.flush()
        citers.append(w)
        ref = Reference(
            title="Hub", doi="10.1/hub", resolution_status="local_match", resolved_work_id=hub.id
        )
        db.add(ref)
        db.flush()
        db.add(ReferenceCitation(reference_id=ref.id, citing_work_id=w.id))
    db.commit()

    graph = build_citation_graph(
        db, scope_type="library", node_mode="local_only", visible_ids=None, max_nodes=3
    )
    ids = {n.id for n in graph.nodes}
    assert str(hub.id) in ids  # the hub is the best-connected node — always kept
    assert len(graph.nodes) == 3
    assert graph.summary["nodes_hidden"] == 2


def test_large_scope_citation_graph_is_queued(client, auth_headers, db, monkeypatch) -> None:
    """L-a: a scope above the job threshold answers {queued, job_id} instead of computing inline."""
    from app.models.work import Work
    from app.services.app_config import update_ai_scope_job_threshold

    update_ai_scope_job_threshold(db, value=1)
    db.add_all([Work(canonical_title=f"W{i}", normalized_title=f"w{i}") for i in range(3)])
    db.commit()
    monkeypatch.setattr(
        "app.api.v1.endpoints.graph.enqueue_analysis_graph",
        lambda kind, params, actor_user_id: f"analysis-{kind}-x",
    )
    resp = client.post(
        "/api/v1/graphs/citation",
        headers=auth_headers("owner"),
        json={"scope": {"type": "library"}, "node_mode": "local_only"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["queued"] is True and body["job_id"] == "analysis-citation-x"


def test_color_by_rack_and_multi_membership_groups(db_session) -> None:
    """Rack color-by resolves through the paper's shelves; a paper on several shelves carries ALL
    of them in ``color_groups`` (the UI's color-wheel data) with ``color_group`` = the first."""
    work = Work(canonical_title="Multi", normalized_title="multi")
    db_session.add(work)
    shelf_a = Shelf(name="Alpha", access_level="open")
    shelf_b = Shelf(name="Beta", access_level="open")
    private = Shelf(name="Secret", access_level="private")
    db_session.add_all([shelf_a, shelf_b, private])
    db_session.flush()
    for s in (shelf_a, shelf_b, private):
        db_session.add(ShelfWork(shelf_id=s.id, work_id=work.id))
    rack_a = Rack(name="Rack One", access_level="open")
    rack_hidden = Rack(name="Hidden Rack", access_level="private")
    db_session.add_all([rack_a, rack_hidden])
    db_session.flush()
    db_session.add(RackShelf(rack_id=rack_a.id, shelf_id=shelf_a.id))
    db_session.add(RackShelf(rack_id=rack_hidden.id, shelf_id=shelf_b.id))
    db_session.commit()

    by_shelf = build_citation_graph(
        db_session,
        scope_type="selected_papers",
        work_ids=[work.id],
        compute_metrics=True,
        color_by="shelf",
    )
    node = by_shelf.nodes[0]
    assert node.color_groups == ["Alpha", "Beta"]  # private shelf never surfaces
    assert node.color_group == "Alpha"

    by_rack = build_citation_graph(
        db_session,
        scope_type="selected_papers",
        work_ids=[work.id],
        compute_metrics=True,
        color_by="rack",
    )
    node = by_rack.nodes[0]
    assert node.color_groups == ["Rack One"]  # the private rack never surfaces
    assert node.color_group == "Rack One"


def test_membership_groups_defaults_and_tag_multi(db_session) -> None:
    from app.services.graph_color import membership_groups

    work = Work(canonical_title="Tags", normalized_title="tags")
    bare = Work(canonical_title="Bare", normalized_title="bare")
    db_session.add_all([work, bare])
    t1 = Tag(name="alpha", normalized_name="alpha")
    t2 = Tag(name="beta", normalized_name="beta")
    db_session.add_all([t1, t2])
    db_session.flush()
    db_session.add(TagLink(tag_id=t1.id, entity_type="work", entity_id=work.id))
    db_session.add(TagLink(tag_id=t2.id, entity_type="work", entity_id=work.id))
    db_session.commit()

    groups = membership_groups(db_session, [work.id, bare.id], "tag")
    assert groups[work.id] == ["alpha", "beta"]
    assert groups[bare.id] == ["untagged"]
    assert membership_groups(db_session, [bare.id], "rack")[bare.id] == ["unracked"]
