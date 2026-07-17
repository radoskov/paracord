"""Visualization provider + endpoint tests (D38, Track C P2)."""

from pathlib import Path

import pytest
from app.db.base import Base
from app.models.chunk import WorkChunk
from app.models.citation import Reference, ReferenceCitation
from app.models.organization import Rack, RackShelf, Shelf, ShelfWork
from app.models.source import ImportBatch
from app.models.user import User
from app.models.work import Work
from app.services.visualization import (
    AXIS_LABELS,
    MAX_NODES,
    VizScope,
    available_view_types,
    get_viz,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Heavier suite (file-backed SQLite schema setup), aligned with the citation/topic graph tests.
pytestmark = pytest.mark.slow


@pytest.fixture()
def db_session(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'viz.db'}")
    Base.metadata.create_all(
        bind=engine,
        tables=[
            Work.__table__,
            WorkChunk.__table__,
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
        yield session


def _owner(db) -> User:
    user = User(username="owner", password_hash="x", role="owner")
    db.add(user)
    db.flush()
    return user


def _node_by_id(payload, work_id) -> dict:
    return next(n for n in payload.nodes if n.id == str(work_id))


def test_registry_has_temporal_map() -> None:
    assert "temporal_map" in available_view_types()


def test_temporal_map_axes_map_correctly(db_session, make_reference) -> None:
    actor = _owner(db_session)
    citing = Work(canonical_title="Citing", normalized_title="citing", doi="10.1/citing", year=2020)
    cited = Work(
        canonical_title="Cited",
        normalized_title="cited",
        doi="10.1/cited",
        year=2010,
        citation_count=42,
    )
    db_session.add_all([citing, cited])
    db_session.flush()
    make_reference(db_session, citing_work_id=citing.id, doi="10.1/cited", title="Cited")
    db_session.commit()

    payload = get_viz(
        db_session,
        actor,
        "temporal_map",
        VizScope(type="library"),
        {"x_axis": "year", "y_axis": "citation_count"},
    )

    assert payload.axes == {
        "x": {"key": "year", "label": AXIS_LABELS["year"]},
        "y": {"key": "citation_count", "label": AXIS_LABELS["citation_count"]},
    }
    assert {opt["key"] for opt in payload.axis_options} == set(AXIS_LABELS)
    citing_node = _node_by_id(payload, citing.id)
    cited_node = _node_by_id(payload, cited.id)
    # X = year maps to Work.year; Y = citation_count maps to Work.citation_count.
    assert citing_node.x == 2020.0
    assert cited_node.x == 2010.0
    assert cited_node.y == 42.0
    # citing has no citation_count -> muted (None) on that axis, not excluded.
    assert citing_node.y is None
    assert citing_node.shape == "in_library"


def test_temporal_map_color_by_rack_respects_viewer(db_session) -> None:
    """color_by=rack colours each paper by the racks of its shelves. The owner sees a paper's
    PRIVATE rack (regression: private racks used to collapse to "unracked" for everyone); a reader
    without a grant does not, so a private rack name is never leaked as a colour.
    """
    owner = _owner(db_session)
    reader = User(username="reader", password_hash="x", role="reader")
    db_session.add(reader)
    work = Work(canonical_title="P", normalized_title="p", year=2020)
    db_session.add(work)
    shelf = Shelf(name="Open Shelf", access_level="open")
    db_session.add(shelf)
    db_session.flush()
    db_session.add(ShelfWork(shelf_id=shelf.id, work_id=work.id))
    rack = Rack(name="Private Rack", access_level="private")
    db_session.add(rack)
    db_session.flush()
    db_session.add(RackShelf(rack_id=rack.id, shelf_id=shelf.id))
    db_session.commit()

    def node(actor: User) -> dict:
        payload = get_viz(
            db_session, actor, "temporal_map", VizScope(type="library"), {"color_by": "rack"}
        )
        return _node_by_id(payload, work.id)

    owner_node = node(owner)
    assert owner_node.color_group == "Private Rack"  # owner sees their own private rack
    assert owner_node.color_groups == ["Private Rack"]

    assert node(reader).color_group == "unracked"  # reader never sees the private rack name


def test_local_degree_axis_counts_incoming_citations(db_session, make_reference) -> None:
    actor = _owner(db_session)
    a = Work(canonical_title="A", normalized_title="a", doi="10.1/a", year=2019)
    b = Work(canonical_title="B", normalized_title="b", doi="10.1/b", year=2018)
    cited = Work(canonical_title="Seed", normalized_title="seed", doi="10.1/seed", year=2001)
    db_session.add_all([a, b, cited])
    db_session.flush()
    # Both A and B cite the seed -> its local degree is 2 (distinct in-library citing papers).
    make_reference(db_session, citing_work_id=a.id, doi="10.1/seed", title="Seed")
    make_reference(db_session, citing_work_id=b.id, doi="10.1/seed", title="Seed")
    db_session.commit()

    payload = get_viz(
        db_session,
        actor,
        "temporal_map",
        VizScope(type="library"),
        {"x_axis": "year", "y_axis": "local_degree", "include_edges": True},
    )
    seed_node = _node_by_id(payload, cited.id)
    assert seed_node.y == 2.0
    assert seed_node.meta["local_degree"] == 2
    assert _node_by_id(payload, a.id).y == 0.0
    # Edge overlay reuses the citation graph's resolved local edges.
    assert payload.edges is not None
    assert {(e.source, e.target) for e in payload.edges} == {
        (str(a.id), str(cited.id)),
        (str(b.id), str(cited.id)),
    }


def test_citation_velocity_axis(db_session) -> None:
    actor = _owner(db_session)
    work = Work(
        canonical_title="V", normalized_title="v", doi="10.1/v", year=2010, citation_count=100
    )
    no_year = Work(canonical_title="N", normalized_title="n", doi="10.1/n", citation_count=5)
    db_session.add_all([work, no_year])
    db_session.commit()

    payload = get_viz(
        db_session,
        actor,
        "temporal_map",
        VizScope(type="library"),
        {"x_axis": "citation_velocity", "y_axis": "year", "current_year": 2020},
    )
    # 100 / max(1, 2020-2010) = 10.0
    assert _node_by_id(payload, work.id).x == 10.0
    # Missing year -> velocity unavailable (None), not an error.
    assert _node_by_id(payload, no_year.id).x is None


def test_similarity_axes_unavailable_without_focus(db_session) -> None:
    actor = _owner(db_session)
    work = Work(canonical_title="S", normalized_title="s", year=2015)
    db_session.add(work)
    db_session.commit()

    for axis in ("similarity_to_focus", "topic_similarity_to_focus"):
        payload = get_viz(
            db_session,
            actor,
            "temporal_map",
            VizScope(type="library"),
            {"x_axis": axis, "y_axis": "year"},
        )
        assert _node_by_id(payload, work.id).x is None
        # The note is user-facing (no raw axis key): it says the axis is unavailable and points at
        # the focus paper as the thing to fix.
        assert any(
            "unavailable" in note.lower() and "focus" in note.lower() for note in payload.notes
        )


def test_topic_similarity_to_focus_jaccard(db_session) -> None:
    actor = _owner(db_session)
    focus = Work(canonical_title="F", normalized_title="f", year=2016, topics=["nlp", "attention"])
    near = Work(canonical_title="Near", normalized_title="near", year=2017, topics=["nlp", "rnn"])
    far = Work(canonical_title="Far", normalized_title="far", year=2018, topics=["biology"])
    db_session.add_all([focus, near, far])
    db_session.commit()

    payload = get_viz(
        db_session,
        actor,
        "temporal_map",
        VizScope(type="library"),
        {"x_axis": "topic_similarity_to_focus", "y_axis": "year", "focus_work_id": focus.id},
    )
    # {nlp, attention} vs {nlp, rnn}: |∩|=1, |∪|=3 -> 1/3.
    assert _node_by_id(payload, near.id).x == pytest.approx(1 / 3, abs=1e-4)
    # No topic overlap -> 0.
    assert _node_by_id(payload, far.id).x == 0.0


def test_keyword_similarity_to_focus_jaccard(db_session) -> None:
    """5b: keyword-similarity axis is a Jaccard overlap of Work.keywords vs the focus paper."""
    actor = _owner(db_session)
    focus = Work(
        canonical_title="F", normalized_title="f", year=2016, keywords=["nlp", "attention"]
    )
    near = Work(canonical_title="Near", normalized_title="near", year=2017, keywords=["nlp", "rnn"])
    far = Work(canonical_title="Far", normalized_title="far", year=2018, keywords=["biology"])
    db_session.add_all([focus, near, far])
    db_session.commit()

    payload = get_viz(
        db_session,
        actor,
        "temporal_map",
        VizScope(type="library"),
        {"x_axis": "keyword_similarity_to_focus", "y_axis": "year", "focus_work_id": focus.id},
    )
    assert _node_by_id(payload, near.id).x == pytest.approx(1 / 3, abs=1e-4)
    assert _node_by_id(payload, far.id).x == 0.0
    # The axis is advertised in the shared option set.
    assert "keyword_similarity_to_focus" in {opt["key"] for opt in payload.axis_options}


def test_keyword_similarity_axis_unavailable_without_focus(db_session) -> None:
    actor = _owner(db_session)
    work = Work(canonical_title="S", normalized_title="s", year=2015, keywords=["x"])
    db_session.add(work)
    db_session.commit()
    payload = get_viz(
        db_session,
        actor,
        "temporal_map",
        VizScope(type="library"),
        {"x_axis": "keyword_similarity_to_focus", "y_axis": "year"},
    )
    assert _node_by_id(payload, work.id).x is None
    assert any("unavailable" in n.lower() and "focus" in n.lower() for n in payload.notes)


def test_size_by_year_and_color_by_venue_and_year(db_session) -> None:
    """5j size-by-year; 5d colour-by-venue; 5h colour-by-year (discrete per-year)."""
    actor = _owner(db_session)
    work = Work(
        canonical_title="E",
        normalized_title="e",
        year=2015,
        venue="ICRA",
        reading_status="reading",
    )
    db_session.add(work)
    db_session.commit()

    by_year_size = get_viz(
        db_session, actor, "temporal_map", VizScope(type="library"), {"size_by": "year"}
    )
    assert _node_by_id(by_year_size, work.id).size == 2015.0

    by_venue = get_viz(
        db_session, actor, "temporal_map", VizScope(type="library"), {"color_by": "venue"}
    )
    assert _node_by_id(by_venue, work.id).color_group == "ICRA"

    by_year_color = get_viz(
        db_session, actor, "temporal_map", VizScope(type="library"), {"color_by": "year"}
    )
    assert _node_by_id(by_year_color, work.id).color_group == "2015"


def test_encodings_size_and_color(db_session) -> None:
    actor = _owner(db_session)
    work = Work(
        canonical_title="E",
        normalized_title="e",
        year=2015,
        citation_count=7,
        reading_status="reading",
    )
    db_session.add(work)
    db_session.commit()

    payload = get_viz(
        db_session,
        actor,
        "temporal_map",
        VizScope(type="library"),
        {"size_by": "citation_count", "color_by": "status"},
    )
    node = _node_by_id(payload, work.id)
    assert node.size == 7.0
    assert node.color_group == "reading"
    assert payload.legend == {"color_by": "status", "groups": ["reading"]}


def test_node_cap_truncates_with_note(db_session) -> None:
    actor = _owner(db_session)
    works = [
        Work(canonical_title=f"W{i}", normalized_title=f"w{i:03d}", year=2000 + i) for i in range(5)
    ]
    db_session.add_all(works)
    db_session.commit()

    payload = get_viz(
        db_session,
        actor,
        "temporal_map",
        VizScope(type="library"),
        {"max_nodes": 3},
    )
    assert len(payload.nodes) == 3
    assert any("node cap" in note for note in payload.notes)


def test_scope_filters_to_shelf(db_session) -> None:
    actor = _owner(db_session)
    on_shelf = Work(canonical_title="On", normalized_title="on", year=2015)
    loose = Work(canonical_title="Loose", normalized_title="loose", year=2016)
    db_session.add_all([on_shelf, loose])
    shelf = Shelf(name="Scope")
    db_session.add(shelf)
    db_session.flush()
    db_session.add(ShelfWork(shelf_id=shelf.id, work_id=on_shelf.id))
    db_session.commit()

    payload = get_viz(db_session, actor, "temporal_map", VizScope(type="shelf", id=shelf.id), {})
    assert {n.id for n in payload.nodes} == {str(on_shelf.id)}


def test_see_filter_hides_private_shelf_work_from_reader(db_session) -> None:
    reader = User(username="reader", password_hash="x", role="reader")
    db_session.add(reader)
    hidden = Work(canonical_title="Hidden", normalized_title="hidden", year=2015)
    loose = Work(canonical_title="Loose", normalized_title="loose", year=2016)
    db_session.add_all([hidden, loose])
    private_shelf = Shelf(name="Private", access_level="private")
    db_session.add(private_shelf)
    db_session.flush()
    db_session.add(ShelfWork(shelf_id=private_shelf.id, work_id=hidden.id))
    db_session.commit()

    payload = get_viz(db_session, reader, "temporal_map", VizScope(type="library"), {})
    # The reader may not SEE the private-shelf work; only the loose (open) paper appears.
    ids = {n.id for n in payload.nodes}
    assert str(hidden.id) not in ids
    assert str(loose.id) in ids


def test_unknown_view_type_raises(db_session) -> None:
    actor = _owner(db_session)
    with pytest.raises(ValueError, match="Unknown visualization view type"):
        get_viz(db_session, actor, "nope", VizScope(type="library"), {})


def test_max_nodes_default_constant() -> None:
    assert MAX_NODES == 500


# --------------------------------------------------------------------------------------------------
# P3 — embedding-cluster map (PCA-2D).
# --------------------------------------------------------------------------------------------------
import numpy as np  # noqa: E402
from app.services import visualization as viz  # noqa: E402
from app.services.visualization import _pca_2d  # noqa: E402


def test_registry_has_embedding_cluster() -> None:
    assert "embedding_cluster" in available_view_types()


def test_pca_2d_deterministic_and_separates_groups() -> None:
    # Three clearly-separated 6-D groups: axis-aligned centroids with tiny per-point jitter.
    base = np.array(
        [
            [10.0, 0, 0, 0, 0, 0],
            [0, 10.0, 0, 0, 0, 0],
            [0, 0, 10.0, 0, 0, 0],
        ]
    )
    jitter = np.array([[0.01, 0, 0, 0, 0, 0], [0, 0, 0.01, 0, 0, 0], [0, 0, 0, 0, 0, 0]])
    matrix = np.vstack([base[0] + jitter, base[1] + jitter, base[2] + jitter])
    coords = _pca_2d(matrix)

    assert coords.shape == (9, 2)
    # Deterministic: identical input -> identical output (fixed component signs).
    assert np.array_equal(coords, _pca_2d(matrix))

    groups = [coords[0:3], coords[3:6], coords[6:9]]
    centroids = [g.mean(axis=0) for g in groups]
    within = max(
        np.linalg.norm(g - c, axis=1).max() for g, c in zip(groups, centroids, strict=True)
    )
    between = min(
        np.linalg.norm(centroids[i] - centroids[j]) for i in range(3) for j in range(i + 1, 3)
    )
    # Each group is far tighter than the gap between groups -> distinguishable in 2-D.
    assert between > 10 * within


def test_pca_2d_pads_single_component() -> None:
    # Points along one line span a single component; the second column is padded with zeros.
    matrix = np.array([[0.0, 0.0], [1.0, 1.0], [2.0, 2.0]])
    coords = _pca_2d(matrix)
    assert coords.shape == (3, 2)
    assert np.allclose(coords[:, 1], 0.0)


def _patch_dense_vectors(monkeypatch, vector_by_title, *, label="st:fake", skipped=0):
    """Make ``_paper_dense_vectors`` return controlled dense vectors keyed by work title."""

    def _fake(db, works, embedding_model):
        kept = [w for w in works if w.canonical_title in vector_by_title]
        vectors = [
            {i: float(x) for i, x in enumerate(vector_by_title[w.canonical_title])} for w in kept
        ]
        return vectors, kept, label, skipped

    monkeypatch.setattr(viz, "_paper_dense_vectors", _fake)


def test_embedding_cluster_axes_are_fixed_pca_components(db_session, monkeypatch) -> None:
    actor = _owner(db_session)
    works = [
        Work(canonical_title="A", normalized_title="a", year=2020),
        Work(canonical_title="B", normalized_title="b", year=2019),
    ]
    db_session.add_all(works)
    db_session.commit()
    _patch_dense_vectors(monkeypatch, {"A": [1.0, 0.0, 0.0], "B": [0.0, 1.0, 0.0]})
    viz._LAYOUT_CACHE.clear()

    payload = get_viz(db_session, actor, "embedding_cluster", VizScope(type="library"), {})
    assert payload.axes == {
        "x": {"key": "component_1", "label": "Component 1"},
        "y": {"key": "component_2", "label": "Component 2"},
    }
    # Fixed axes -> no swappable option set.
    assert payload.axis_options is None
    assert {n.id for n in payload.nodes} == {str(w.id) for w in works}
    for node in payload.nodes:
        assert node.x is not None and node.y is not None
        assert node.color_group is not None
    assert payload.legend is not None and payload.legend["color_by"] == "cluster"


def test_embedding_cluster_skips_unindexed_with_note(db_session, monkeypatch) -> None:
    actor = _owner(db_session)
    indexed = Work(canonical_title="Indexed", normalized_title="indexed", year=2020)
    absent = Work(canonical_title="Absent", normalized_title="absent", year=2019)
    db_session.add_all([indexed, absent])
    db_session.commit()
    # Only "Indexed" has a stored vector; the other is reported as un-indexed (D19), not placed.
    _patch_dense_vectors(monkeypatch, {"Indexed": [1.0, 0.0]}, skipped=1)
    viz._LAYOUT_CACHE.clear()

    payload = get_viz(db_session, actor, "embedding_cluster", VizScope(type="library"), {})
    assert {n.id for n in payload.nodes} == {str(indexed.id)}
    # B2: the un-indexed paper has no chunks (no extracted text), so it lands in the "needs a PDF +
    # extraction" bucket — reindexing alone can't include it — rather than a bare "reindex" note.
    assert payload.reindex_hint is not None
    needs = payload.reindex_hint["needs_text"]
    assert [p["title"] for p in needs] == ["Absent"]
    assert payload.reindex_hint["reindexable"] == 0


def test_reindex_hint_splits_reindexable_from_needs_pdf(db_session, monkeypatch) -> None:
    """B2: an un-indexed paper WITH extracted chunks is 'reindexable'; one WITHOUT chunks needs a
    PDF + extraction (listed by title so the user can open + extract it)."""
    actor = _owner(db_session)
    indexed = Work(canonical_title="Indexed", normalized_title="indexed", year=2021)
    has_text = Work(canonical_title="HasText", normalized_title="hastext", year=2020)
    no_text = Work(canonical_title="NoText", normalized_title="notext", year=2019)
    db_session.add_all([indexed, has_text, no_text])
    db_session.commit()
    db_session.add(WorkChunk(work_id=has_text.id, section="Methods", position=0, text="body text"))
    db_session.commit()
    _patch_dense_vectors(monkeypatch, {"Indexed": [1.0, 0.0]}, skipped=2)
    viz._LAYOUT_CACHE.clear()

    payload = get_viz(db_session, actor, "embedding_cluster", VizScope(type="library"), {})
    hint = payload.reindex_hint
    assert hint is not None
    assert hint["reindexable"] == 1  # HasText has chunks → a reindex includes it
    assert [p["title"] for p in hint["needs_text"]] == ["NoText"]  # NoText needs a PDF first


def test_embedding_cluster_see_filter_hides_private_work(db_session) -> None:
    reader = User(username="reader", password_hash="x", role="reader")
    db_session.add(reader)
    hidden = Work(canonical_title="Hidden", normalized_title="hidden", year=2015)
    loose = Work(canonical_title="Loose", normalized_title="loose", year=2016)
    db_session.add_all([hidden, loose])
    private_shelf = Shelf(name="Private", access_level="private")
    db_session.add(private_shelf)
    db_session.flush()
    db_session.add(ShelfWork(shelf_id=private_shelf.id, work_id=hidden.id))
    db_session.commit()
    viz._LAYOUT_CACHE.clear()

    # Baseline embedder fallback (no real model) still places the visible papers.
    payload = get_viz(db_session, reader, "embedding_cluster", VizScope(type="library"), {})
    ids = {n.id for n in payload.nodes}
    assert str(hidden.id) not in ids
    assert str(loose.id) in ids


def test_embedding_cluster_uses_baseline_embedder_with_note(db_session) -> None:
    actor = _owner(db_session)
    db_session.add_all(
        [
            Work(canonical_title="Neural machine translation", normalized_title="nmt", year=2016),
            Work(
                canonical_title="Convolutional image recognition", normalized_title="cnn", year=2015
            ),
        ]
    )
    db_session.commit()
    viz._LAYOUT_CACHE.clear()

    payload = get_viz(db_session, actor, "embedding_cluster", VizScope(type="library"), {})
    assert len(payload.nodes) == 2
    assert any("baseline embedder" in note for note in payload.notes)


def test_embedding_cluster_cache_reuses_layout(db_session, monkeypatch) -> None:
    actor = _owner(db_session)
    db_session.add_all(
        [
            Work(canonical_title="A", normalized_title="a", year=2020),
            Work(canonical_title="B", normalized_title="b", year=2019),
        ]
    )
    db_session.commit()
    _patch_dense_vectors(monkeypatch, {"A": [1.0, 0.0, 0.0], "B": [0.0, 1.0, 0.0]})
    viz._LAYOUT_CACHE.clear()

    first = get_viz(db_session, actor, "embedding_cluster", VizScope(type="library"), {})
    assert len(viz._LAYOUT_CACHE) == 1

    # A cache hit must not recompute the projection: make PCA blow up, then require the repeat call
    # to succeed with byte-identical coordinates (served from the cache).
    def _boom(_matrix):
        raise AssertionError("PCA recomputed on a cache hit")

    monkeypatch.setattr(viz, "_pca_2d", _boom)
    second = get_viz(db_session, actor, "embedding_cluster", VizScope(type="library"), {})
    assert len(viz._LAYOUT_CACHE) == 1
    coords_first = {n.id: (n.x, n.y) for n in first.nodes}
    coords_second = {n.id: (n.x, n.y) for n in second.nodes}
    assert coords_first == coords_second


def test_embedding_cluster_cache_invalidates_on_vector_change(db_session, monkeypatch) -> None:
    actor = _owner(db_session)
    db_session.add_all(
        [
            Work(canonical_title="A", normalized_title="a", year=2020),
            Work(canonical_title="B", normalized_title="b", year=2019),
            Work(canonical_title="C", normalized_title="c", year=2018),
        ]
    )
    db_session.commit()
    viz._LAYOUT_CACHE.clear()

    _patch_dense_vectors(
        monkeypatch, {"A": [1.0, 0.0, 0.0], "B": [0.0, 1.0, 0.0], "C": [0.0, 0.0, 1.0]}
    )
    first = get_viz(db_session, actor, "embedding_cluster", VizScope(type="library"), {})
    assert len(viz._LAYOUT_CACHE) == 1

    # Same scope + model, but the stored vectors changed -> the fingerprint mismatches, so the layout
    # is recomputed and overwritten (not served stale). Same cache key -> still one entry.
    _patch_dense_vectors(
        monkeypatch, {"A": [5.0, 1.0, 0.0], "B": [0.0, 1.0, 0.0], "C": [0.0, 0.0, 1.0]}
    )
    second = get_viz(db_session, actor, "embedding_cluster", VizScope(type="library"), {})
    assert len(viz._LAYOUT_CACHE) == 1
    coords_first = {n.id: (n.x, n.y) for n in first.nodes}
    coords_second = {n.id: (n.x, n.y) for n in second.nodes}
    assert coords_first != coords_second


def test_embedding_cluster_node_cap_samples_with_note(db_session, monkeypatch) -> None:
    actor = _owner(db_session)
    works = [
        Work(canonical_title=f"W{i:02d}", normalized_title=f"w{i:03d}", year=2000 + i)
        for i in range(6)
    ]
    db_session.add_all(works)
    db_session.commit()
    _patch_dense_vectors(
        monkeypatch, {w.canonical_title: [float(i), float(6 - i)] for i, w in enumerate(works)}
    )
    viz._LAYOUT_CACHE.clear()

    payload = get_viz(
        db_session, actor, "embedding_cluster", VizScope(type="library"), {"max_nodes": 3}
    )
    assert len(payload.nodes) == 3
    assert any("Sampled 3 of 6" in note and "node cap" in note for note in payload.notes)


def test_endpoint_embedding_cluster_lists_and_builds(client, db, auth_headers) -> None:
    headers = auth_headers("owner")
    db.add(Work(canonical_title="Endpoint cluster", normalized_title="epc", year=2015))
    db.commit()

    listed = client.get("/api/v1/viz/", headers=headers)
    assert "embedding_cluster" in listed.json()["view_types"]

    response = client.get("/api/v1/viz/embedding_cluster", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["view_type"] == "embedding_cluster"
    assert body["axes"]["x"]["key"] == "component_1"
    assert body["axis_options"] is None


# --- P5b: UMAP opt-in layout (umap-learn NOT installed in the test image) --------------------------


def test_umap_layout_falls_back_to_pca_when_absent(db_session, monkeypatch) -> None:
    actor = _owner(db_session)
    db_session.add_all(
        [
            Work(canonical_title="A", normalized_title="a", year=2020),
            Work(canonical_title="B", normalized_title="b", year=2019),
            Work(canonical_title="C", normalized_title="c", year=2018),
        ]
    )
    db_session.commit()
    _patch_dense_vectors(
        monkeypatch, {"A": [1.0, 0.0, 0.0], "B": [0.0, 1.0, 0.0], "C": [0.0, 0.0, 1.0]}
    )
    # umap-learn is not installed in the test image; be explicit so the test never depends on env.
    monkeypatch.setattr(viz, "_umap_available", lambda: False)
    viz._LAYOUT_CACHE.clear()

    payload = get_viz(
        db_session, actor, "embedding_cluster", VizScope(type="library"), {"layout": "umap"}
    )
    # Rendered via PCA, and the reason is surfaced honestly.
    assert payload.legend["layout"] == "pca"
    assert any("UMAP" in note and "PCA" in note for note in payload.notes)
    assert all(n.x is not None and n.y is not None for n in payload.nodes)


def test_umap_layout_used_when_available(db_session, monkeypatch) -> None:
    actor = _owner(db_session)
    db_session.add_all(
        [
            Work(canonical_title="A", normalized_title="a", year=2020),
            Work(canonical_title="B", normalized_title="b", year=2019),
            Work(canonical_title="C", normalized_title="c", year=2018),
        ]
    )
    db_session.commit()
    _patch_dense_vectors(
        monkeypatch, {"A": [1.0, 0.0, 0.0], "B": [0.0, 1.0, 0.0], "C": [0.0, 0.0, 1.0]}
    )
    # Simulate umap-learn being installed with a deterministic stand-in projection.
    monkeypatch.setattr(viz, "_umap_available", lambda: True)
    monkeypatch.setattr(
        viz, "_umap_2d", lambda matrix: np.arange(matrix.shape[0] * 2, dtype=float).reshape(-1, 2)
    )
    viz._LAYOUT_CACHE.clear()

    payload = get_viz(
        db_session, actor, "embedding_cluster", VizScope(type="library"), {"layout": "umap"}
    )
    assert payload.legend["layout"] == "umap"
    assert not any("UMAP" in note for note in payload.notes)


def test_layout_cache_keyed_by_layout(db_session, monkeypatch) -> None:
    actor = _owner(db_session)
    db_session.add_all(
        [
            Work(canonical_title="A", normalized_title="a", year=2020),
            Work(canonical_title="B", normalized_title="b", year=2019),
            Work(canonical_title="C", normalized_title="c", year=2018),
        ]
    )
    db_session.commit()
    _patch_dense_vectors(
        monkeypatch, {"A": [1.0, 0.0, 0.0], "B": [0.0, 1.0, 0.0], "C": [0.0, 0.0, 1.0]}
    )
    monkeypatch.setattr(viz, "_umap_available", lambda: True)
    monkeypatch.setattr(
        viz, "_umap_2d", lambda matrix: np.arange(matrix.shape[0] * 2, dtype=float).reshape(-1, 2)
    )
    viz._LAYOUT_CACHE.clear()

    get_viz(db_session, actor, "embedding_cluster", VizScope(type="library"), {"layout": "pca"})
    get_viz(db_session, actor, "embedding_cluster", VizScope(type="library"), {"layout": "umap"})
    # PCA and UMAP cache independently — two entries for the same scope/model.
    assert len(viz._LAYOUT_CACHE) == 2


def test_unknown_layout_raises(db_session) -> None:
    actor = _owner(db_session)
    db_session.add(Work(canonical_title="A", normalized_title="a", year=2020))
    db_session.commit()
    viz._LAYOUT_CACHE.clear()
    with pytest.raises(ValueError, match="Unknown layout"):
        get_viz(
            db_session, actor, "embedding_cluster", VizScope(type="library"), {"layout": "tsne"}
        )


def test_endpoint_umap_layout_degrades_to_pca(client, db, auth_headers) -> None:
    headers = auth_headers("owner")
    db.add_all(
        [
            Work(canonical_title="Neural machine translation", normalized_title="nmt", year=2016),
            Work(canonical_title="Convolutional recognition", normalized_title="cnn", year=2015),
            Work(canonical_title="Graph attention networks", normalized_title="gat", year=2018),
        ]
    )
    db.commit()
    viz._LAYOUT_CACHE.clear()

    response = client.get("/api/v1/viz/embedding_cluster?layout=umap", headers=headers)
    assert response.status_code == 200
    body = response.json()
    # umap-learn absent in the test image → server degrades to PCA with a note.
    assert body["legend"]["layout"] == "pca"
    assert any("UMAP" in note for note in body["notes"])


# --------------------------------------------------------------------------------------------------
# Endpoint (HTTP/auth) tests — full app against the shared in-memory DB (conftest fixtures).
# --------------------------------------------------------------------------------------------------
def test_endpoint_requires_auth(client) -> None:
    assert client.get("/api/v1/viz/temporal_map").status_code == 401


def test_endpoint_builds_payload(client, db, auth_headers) -> None:
    headers = auth_headers("owner")
    work = Work(
        canonical_title="Endpoint", normalized_title="endpoint", year=2015, citation_count=3
    )
    db.add(work)
    db.commit()

    response = client.get(
        "/api/v1/viz/temporal_map",
        params={"x_axis": "year", "y_axis": "citation_count"},
        headers=headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["view_type"] == "temporal_map"
    assert body["axes"]["x"]["key"] == "year"
    assert {opt["key"] for opt in body["axis_options"]} == set(AXIS_LABELS)
    node = next(n for n in body["nodes"] if n["id"] == str(work.id))
    assert node["x"] == 2015.0
    assert node["y"] == 3.0


def test_endpoint_list_view_types(client, auth_headers) -> None:
    response = client.get("/api/v1/viz/", headers=auth_headers("reader"))
    assert response.status_code == 200
    assert "temporal_map" in response.json()["view_types"]


def test_endpoint_node_cap_note(client, db, auth_headers) -> None:
    headers = auth_headers("owner")
    db.add_all(
        [
            Work(canonical_title=f"C{i}", normalized_title=f"c{i:03d}", year=2000 + i)
            for i in range(4)
        ]
    )
    db.commit()

    response = client.get("/api/v1/viz/temporal_map", params={"max_nodes": 2}, headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert len(body["nodes"]) == 2
    assert any("node cap" in note for note in body["notes"])


def test_endpoint_unknown_view_type_404(client, auth_headers) -> None:
    response = client.get("/api/v1/viz/does_not_exist", headers=auth_headers("owner"))
    assert response.status_code == 404


def test_endpoint_bad_axis_400(client, auth_headers) -> None:
    response = client.get(
        "/api/v1/viz/temporal_map", params={"x_axis": "bogus"}, headers=auth_headers("owner")
    )
    assert response.status_code == 400


def test_endpoint_private_shelf_scope_404_for_reader(client, db, auth_headers) -> None:
    # A reader asking for a private shelf they cannot SEE gets a 404 (scope container guard).
    shelf = Shelf(name="Secret", access_level="private")
    db.add(shelf)
    db.commit()
    response = client.get(
        "/api/v1/viz/temporal_map",
        params={"scope_type": "shelf", "scope_id": str(shelf.id)},
        headers=auth_headers("reader"),
    )
    assert response.status_code == 404


# --------------------------------------------------------------------------------------------------
# P5a — co-citation / coupling network, topic river, similarity heatmap.
# --------------------------------------------------------------------------------------------------
def _edge_map(payload) -> dict:
    return {tuple(sorted((e.source, e.target))): e.weight for e in payload.edges}


def test_registry_has_p5a_views() -> None:
    types = available_view_types()
    assert {"co_citation", "topic_river", "similarity_heatmap"} <= set(types)


def test_co_citation_coupling_links_works_sharing_a_reference(db_session, make_reference) -> None:
    actor = _owner(db_session)
    a = Work(canonical_title="A", normalized_title="a", doi="10.1/a", year=2020)
    b = Work(canonical_title="B", normalized_title="b", doi="10.1/b", year=2019)
    c = Work(canonical_title="C", normalized_title="c", doi="10.1/c", year=2018)
    db_session.add_all([a, b, c])
    db_session.flush()
    # A and B both cite the same external work -> bibliographic coupling (weight = 1 shared ref).
    make_reference(db_session, citing_work_id=a.id, doi="10.9/shared", title="Shared classic")
    make_reference(db_session, citing_work_id=b.id, doi="10.9/shared", title="Shared classic")
    make_reference(db_session, citing_work_id=c.id, doi="10.9/other", title="Unrelated")
    db_session.commit()

    payload = get_viz(
        db_session, actor, "co_citation", VizScope(type="library"), {"edge_context": "coupling"}
    )
    edges = _edge_map(payload)
    assert edges == {tuple(sorted((str(a.id), str(b.id)))): 1.0}
    # size == coupling degree: A and B have one neighbour each, C is isolated.
    assert _node_by_id(payload, a.id).size == 1.0
    assert _node_by_id(payload, b.id).size == 1.0
    assert _node_by_id(payload, c.id).size == 0.0
    # Node-link view: no fixed coordinates.
    assert _node_by_id(payload, a.id).x is None


def test_co_citation_links_works_cited_together(db_session, make_reference) -> None:
    actor = _owner(db_session)
    a = Work(canonical_title="A", normalized_title="a", doi="10.1/a", year=2020)
    b = Work(canonical_title="B", normalized_title="b", doi="10.1/b", year=2019)
    citer = Work(canonical_title="Citer", normalized_title="citer", doi="10.1/citer", year=2021)
    db_session.add_all([a, b, citer])
    db_session.flush()
    # One paper cites both A and B -> A and B are co-cited (weight = 1 shared citer).
    make_reference(db_session, citing_work_id=citer.id, doi="10.1/a", title="A")
    make_reference(db_session, citing_work_id=citer.id, doi="10.1/b", title="B")
    db_session.commit()

    payload = get_viz(
        db_session, actor, "co_citation", VizScope(type="library"), {"edge_context": "co_citation"}
    )
    edges = _edge_map(payload)
    assert edges == {tuple(sorted((str(a.id), str(b.id)))): 1.0}
    assert any("in-library citers" in note for note in payload.notes)


def test_co_citation_unknown_edge_context_raises(db_session) -> None:
    actor = _owner(db_session)
    db_session.add(Work(canonical_title="X", normalized_title="x", year=2020))
    db_session.commit()
    with pytest.raises(ValueError, match="Unknown edge context"):
        get_viz(
            db_session, actor, "co_citation", VizScope(type="library"), {"edge_context": "bogus"}
        )


def test_co_citation_see_filter_hides_private_work(db_session) -> None:
    reader = User(username="reader", password_hash="x", role="reader")
    db_session.add(reader)
    hidden = Work(canonical_title="Hidden", normalized_title="hidden", year=2015)
    loose = Work(canonical_title="Loose", normalized_title="loose", year=2016)
    db_session.add_all([hidden, loose])
    private_shelf = Shelf(name="Private", access_level="private")
    db_session.add(private_shelf)
    db_session.flush()
    db_session.add(ShelfWork(shelf_id=private_shelf.id, work_id=hidden.id))
    db_session.commit()

    payload = get_viz(db_session, reader, "co_citation", VizScope(type="library"), {})
    ids = {n.id for n in payload.nodes}
    assert str(hidden.id) not in ids
    assert str(loose.id) in ids


def test_topic_river_shares_sum_to_one_per_year(db_session, monkeypatch) -> None:
    actor = _owner(db_session)
    works = [
        Work(canonical_title="A", normalized_title="a", year=2020),
        Work(canonical_title="B", normalized_title="b", year=2020),
        Work(canonical_title="C", normalized_title="c", year=2021),
    ]
    db_session.add_all(works)
    db_session.commit()
    _patch_dense_vectors(
        monkeypatch, {"A": [1.0, 0.0, 0.0], "B": [0.0, 1.0, 0.0], "C": [0.0, 0.0, 1.0]}
    )
    viz._LAYOUT_CACHE.clear()

    payload = get_viz(db_session, actor, "topic_river", VizScope(type="library"), {})
    series = payload.series
    assert series is not None
    assert series["years"] == [2020, 2021]
    assert payload.nodes == []
    # Each year's topic shares sum to 1 (a topic is a partition of that year's papers).
    for idx in range(len(series["years"])):
        col_sum = sum(topic["values"][idx] for topic in series["topics"])
        assert col_sum == pytest.approx(1.0, abs=1e-3)
    # Every topic row aligns to the year axis.
    for topic in series["topics"]:
        assert len(topic["values"]) == len(series["years"])


def test_topic_river_excludes_papers_without_year(db_session, monkeypatch) -> None:
    actor = _owner(db_session)
    dated = Work(canonical_title="Dated", normalized_title="dated", year=2020)
    undated = Work(canonical_title="Undated", normalized_title="undated")
    db_session.add_all([dated, undated])
    db_session.commit()
    _patch_dense_vectors(monkeypatch, {"Dated": [1.0, 0.0], "Undated": [0.0, 1.0]})
    viz._LAYOUT_CACHE.clear()

    payload = get_viz(db_session, actor, "topic_river", VizScope(type="library"), {})
    assert payload.series["years"] == [2020]
    assert any("no publication year" in note for note in payload.notes)


def test_topic_river_see_filter_hides_private_work(db_session) -> None:
    reader = User(username="reader", password_hash="x", role="reader")
    db_session.add(reader)
    hidden = Work(canonical_title="Hidden", normalized_title="hidden", year=2015)
    loose = Work(canonical_title="Loose", normalized_title="loose", year=2016)
    db_session.add_all([hidden, loose])
    private_shelf = Shelf(name="Private", access_level="private")
    db_session.add(private_shelf)
    db_session.flush()
    db_session.add(ShelfWork(shelf_id=private_shelf.id, work_id=hidden.id))
    db_session.commit()
    viz._LAYOUT_CACHE.clear()

    # Baseline embedder places both papers if visible; the reader only sees the loose one, so its
    # year is the only one in the stream (the hidden 2015 paper never contributes).
    payload = get_viz(db_session, reader, "topic_river", VizScope(type="library"), {})
    assert payload.series is not None
    assert payload.series["years"] == [2016]


def test_similarity_heatmap_symmetric_unit_diagonal(db_session, monkeypatch) -> None:
    actor = _owner(db_session)
    works = [
        Work(canonical_title="A", normalized_title="a", year=2020),
        Work(canonical_title="B", normalized_title="b", year=2019),
    ]
    db_session.add_all(works)
    db_session.commit()
    _patch_dense_vectors(monkeypatch, {"A": [1.0, 0.0], "B": [1.0, 1.0]})
    viz._LAYOUT_CACHE.clear()

    payload = get_viz(db_session, actor, "similarity_heatmap", VizScope(type="library"), {})
    matrix = payload.matrix
    assert matrix is not None
    assert matrix["labels"] == ["A", "B"]
    values = matrix["values"]
    n = len(matrix["labels"])
    for i in range(n):
        assert values[i][i] == pytest.approx(1.0)
        for j in range(n):
            assert values[i][j] == pytest.approx(values[j][i])
    # cos([1,0], [1,1]) = 1/sqrt(2).
    assert values[0][1] == pytest.approx(0.7071, abs=1e-3)


def test_similarity_heatmap_caps_selection_by_recency(db_session, monkeypatch) -> None:
    actor = _owner(db_session)
    works = [
        Work(canonical_title=f"W{i:02d}", normalized_title=f"w{i:03d}", year=2000 + i)
        for i in range(4)
    ]
    db_session.add_all(works)
    db_session.commit()
    _patch_dense_vectors(
        monkeypatch, {w.canonical_title: [float(i), float(4 - i)] for i, w in enumerate(works)}
    )
    viz._LAYOUT_CACHE.clear()

    payload = get_viz(
        db_session, actor, "similarity_heatmap", VizScope(type="library"), {"max_nodes": 2}
    )
    assert payload.matrix is not None
    # The two most recent papers (2003, 2002) are kept, most-recent-first.
    assert payload.matrix["labels"] == ["W03", "W02"]
    assert any("most recent" in note and "cap" in note for note in payload.notes)


def test_similarity_heatmap_cap_constant() -> None:
    assert viz.HEATMAP_CAP == 50


def test_similarity_heatmap_see_filter_hides_private_work(db_session) -> None:
    reader = User(username="reader", password_hash="x", role="reader")
    db_session.add(reader)
    hidden = Work(canonical_title="Hidden", normalized_title="hidden", year=2015)
    loose = Work(canonical_title="Loose", normalized_title="loose", year=2016)
    db_session.add_all([hidden, loose])
    private_shelf = Shelf(name="Private", access_level="private")
    db_session.add(private_shelf)
    db_session.flush()
    db_session.add(ShelfWork(shelf_id=private_shelf.id, work_id=hidden.id))
    db_session.commit()
    viz._LAYOUT_CACHE.clear()

    payload = get_viz(db_session, reader, "similarity_heatmap", VizScope(type="library"), {})
    assert payload.matrix is not None
    assert str(hidden.id) not in payload.matrix["ids"]
    assert str(loose.id) in payload.matrix["ids"]


def test_endpoint_builds_p5a_views(client, db, auth_headers, make_reference) -> None:
    headers = auth_headers("owner")
    a = Work(canonical_title="Alpha", normalized_title="alpha", doi="10.2/a", year=2020)
    b = Work(canonical_title="Beta", normalized_title="beta", doi="10.2/b", year=2019)
    db.add_all([a, b])
    db.flush()
    make_reference(db, citing_work_id=a.id, doi="10.9/s", title="S")
    make_reference(db, citing_work_id=b.id, doi="10.9/s", title="S")
    db.commit()

    listed = client.get("/api/v1/viz/", headers=headers).json()["view_types"]
    assert {"co_citation", "topic_river", "similarity_heatmap"} <= set(listed)

    coc = client.get("/api/v1/viz/co_citation", headers=headers)
    assert coc.status_code == 200
    assert coc.json()["view_type"] == "co_citation"
    assert len(coc.json()["edges"]) == 1

    river = client.get("/api/v1/viz/topic_river", headers=headers)
    assert river.status_code == 200
    assert river.json()["series"] is not None

    heat = client.get("/api/v1/viz/similarity_heatmap", headers=headers)
    assert heat.status_code == 200
    assert heat.json()["matrix"] is not None


def test_endpoint_bad_edge_context_400(client, db, auth_headers) -> None:
    db.add(Work(canonical_title="Z", normalized_title="z", year=2020))
    db.commit()
    response = client.get(
        "/api/v1/viz/co_citation",
        params={"edge_context": "nope"},
        headers=auth_headers("owner"),
    )
    assert response.status_code == 400


def test_temporal_map_edges_suppressed_above_edge_limit(db_session, make_reference) -> None:
    """B3: the citation-edge overlay is drawn under the edge limit and suppressed (with a note) when
    the placed papers exceed it — so a large scope stays readable instead of a hairball."""
    actor = _owner(db_session)
    a = Work(canonical_title="A", normalized_title="a", year=2020)
    b = Work(canonical_title="B", normalized_title="b", year=2019)
    db_session.add_all([a, b])
    db_session.flush()
    make_reference(db_session, citing_work_id=a.id, resolved_work_id=b.id, title="B")
    db_session.commit()

    under = get_viz(
        db_session,
        actor,
        "temporal_map",
        VizScope(type="library"),
        {"include_edges": True, "edge_max_nodes": 10},
    )
    assert under.edges  # the resolved A→B citation edge is drawn

    over = get_viz(
        db_session,
        actor,
        "temporal_map",
        VizScope(type="library"),
        {"include_edges": True, "edge_max_nodes": 1},
    )
    assert not over.edges  # 2 papers > limit of 1 → suppressed
    assert any("edge limit" in n.lower() for n in over.notes)
