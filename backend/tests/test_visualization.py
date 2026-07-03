"""Visualization provider + endpoint tests (D38, Track C P2)."""

from pathlib import Path

import pytest
from app.db.base import Base
from app.models.citation import Reference
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
            Reference.__table__,
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


def test_temporal_map_axes_map_correctly(db_session) -> None:
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
    db_session.add(Reference(citing_work_id=citing.id, doi="10.1/cited", title="Cited"))
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


def test_local_degree_axis_counts_incoming_citations(db_session) -> None:
    actor = _owner(db_session)
    a = Work(canonical_title="A", normalized_title="a", doi="10.1/a", year=2019)
    b = Work(canonical_title="B", normalized_title="b", doi="10.1/b", year=2018)
    cited = Work(canonical_title="Seed", normalized_title="seed", doi="10.1/seed", year=2001)
    db_session.add_all([a, b, cited])
    db_session.flush()
    # Both A and B cite the seed -> its local degree is 2 (distinct in-library citing papers).
    db_session.add_all(
        [
            Reference(citing_work_id=a.id, doi="10.1/seed", title="Seed"),
            Reference(citing_work_id=b.id, doi="10.1/seed", title="Seed"),
        ]
    )
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
        assert any(axis in note and "unavailable" in note for note in payload.notes)


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
