"""Per-paper weighted reference graph (B7 / issue_batch_6 #5)."""

from app.models.citation import CitationMention
from app.models.work import Work
from app.services.reference_graph import classify_section


def test_classify_section_buckets() -> None:
    assert classify_section("Abstract") == "abstract"
    assert classify_section("1. Introduction") == "introduction"
    assert classify_section("Related Work") == "related"  # beats the 'method' rule for mixed heads
    assert classify_section("State of the Art") == "related"
    assert classify_section("3. Methods") == "methods"
    assert classify_section("4. Experiments and Results") == "results"
    assert classify_section("Conclusion") == "other"
    assert classify_section(None) == "other"


def test_reference_graph_splits_local_external_with_section_counts(
    client, auth_headers, db, make_reference
) -> None:
    base = Work(canonical_title="Base", normalized_title="base", year=2020)
    local_target = Work(canonical_title="Local Cited", normalized_title="local cited", year=2015)
    db.add_all([base, local_target])
    db.flush()
    ref_local = make_reference(
        db,
        citing_work_id=base.id,
        resolved_work_id=local_target.id,
        title="Local Cited",
        year=2015,
        resolution_status="resolved",
    )
    ref_ext = make_reference(db, citing_work_id=base.id, title="External Ref", year=2010)
    db.add_all(
        [
            CitationMention(
                citing_work_id=base.id, reference_id=ref_local.id, section_label="4. Methods"
            ),
            CitationMention(
                citing_work_id=base.id, reference_id=ref_local.id, section_label="Methods"
            ),
            CitationMention(
                citing_work_id=base.id, reference_id=ref_ext.id, section_label="Related Work"
            ),
        ]
    )
    db.commit()

    r = client.get(f"/api/v1/works/{base.id}/reference-graph", headers=auth_headers("editor"))
    assert r.status_code == 200
    data = r.json()
    assert data["base_work_id"] == str(base.id)
    by_label = {n["label"]: n for n in data["nodes"]}
    assert by_label["Base"]["kind"] == "base"
    assert by_label["Local Cited"]["kind"] == "local"
    assert by_label["Local Cited"]["section_counts"] == {"methods": 2}
    assert by_label["Local Cited"]["mention_count"] == 2
    assert by_label["External Ref"]["kind"] == "external"
    assert by_label["External Ref"]["section_counts"] == {"related": 1}
    # base → each reference (star), 2 references.
    assert sum(1 for e in data["edges"] if e["source"] == str(base.id)) == 2
    # v2 selectable-Y metrics: present (possibly null) on every node; null for the external ref.
    assert by_label["External Ref"]["citation_count"] is None
    assert by_label["External Ref"]["topic_similarity"] is None
    assert set(by_label["Local Cited"]) >= {"citation_count", "local_degree", "topic_similarity"}


def test_reference_graph_selectable_y_metrics(client, auth_headers, db, make_reference) -> None:
    """B7 v2: local reference nodes carry citation_count, local_degree and topic_similarity to the
    base paper; external nodes leave them null (rendered on the 'n/a' lane)."""
    base = Work(
        canonical_title="Base", normalized_title="base", year=2020, topics=["nlp", "attention"]
    )
    cited = Work(
        canonical_title="Cited",
        normalized_title="cited",
        year=2015,
        citation_count=42,
        topics=["nlp", "rnn"],
    )
    other = Work(canonical_title="Other citer", normalized_title="other citer", year=2019)
    db.add_all([base, cited, other])
    db.flush()
    make_reference(
        db,
        citing_work_id=base.id,
        resolved_work_id=cited.id,
        title="Cited",
        resolution_status="resolved",
    )
    # A second in-library paper also cites "cited" → its local degree is 2.
    make_reference(
        db,
        citing_work_id=other.id,
        resolved_work_id=cited.id,
        title="Cited",
        resolution_status="resolved",
    )
    db.commit()

    data = client.get(
        f"/api/v1/works/{base.id}/reference-graph", headers=auth_headers("editor")
    ).json()
    node = next(n for n in data["nodes"] if n["label"] == "Cited")
    assert node["citation_count"] == 42
    assert node["local_degree"] == 2  # base + other both cite it
    # {nlp, attention} vs {nlp, rnn}: |∩|=1, |∪|=3 → 1/3.
    assert node["topic_similarity"] == round(1 / 3, 4)


def test_reference_graph_nodes_carry_venue_and_doi(
    client, auth_headers, db, make_reference
) -> None:
    """5d/5g: nodes expose venue (local: resolved work's venue) and doi for colour-by-venue and the
    click-to-import prefill."""
    base = Work(canonical_title="Base", normalized_title="base", year=2020, venue="NeurIPS")
    cited = Work(
        canonical_title="Cited", normalized_title="cited", year=2015, venue="ICRA", doi="10.1/c"
    )
    db.add_all([base, cited])
    db.flush()
    make_reference(
        db,
        citing_work_id=base.id,
        resolved_work_id=cited.id,
        title="Cited",
        doi="10.1/c",
        resolution_status="resolved",
    )
    make_reference(
        db,
        citing_work_id=base.id,
        title="Some External Paper",
        doi="10.1/ext",
        year=2011,
    )
    db.commit()

    data = client.get(
        f"/api/v1/works/{base.id}/reference-graph", headers=auth_headers("editor")
    ).json()
    base_node = next(n for n in data["nodes"] if n["kind"] == "base")
    assert base_node["venue"] == "NeurIPS"
    local = next(n for n in data["nodes"] if n["label"] == "Cited")
    assert local["venue"] == "ICRA"  # from the resolved work
    assert local["doi"] == "10.1/c"
    external = next(n for n in data["nodes"] if n["label"] == "Some External Paper")
    assert external["venue"] is None  # references don't store a venue column
    assert external["doi"] == "10.1/ext"


def test_reference_graph_local_ref_to_ref_edges_opt_in(
    client, auth_headers, db, make_reference
) -> None:
    """include_ref_edges adds a citation edge between two resolved-local references when one cites
    the other."""
    base = Work(canonical_title="Base", normalized_title="base", year=2021)
    a = Work(canonical_title="A", normalized_title="a", year=2018)
    b = Work(canonical_title="B", normalized_title="b", year=2016)
    db.add_all([base, a, b])
    db.flush()
    make_reference(
        db,
        citing_work_id=base.id,
        resolved_work_id=a.id,
        title="A",
        resolution_status="resolved",
    )
    make_reference(
        db,
        citing_work_id=base.id,
        resolved_work_id=b.id,
        title="B",
        resolution_status="resolved",
    )
    # A cites B (both are references of the base) → a ref→ref edge when opted in.
    make_reference(
        db, citing_work_id=a.id, resolved_work_id=b.id, title="B", resolution_status="resolved"
    )
    db.commit()

    without = client.get(
        f"/api/v1/works/{base.id}/reference-graph", headers=auth_headers("editor")
    ).json()
    # Only base→ref edges (2), no ref→ref.
    assert all(e["source"] == str(base.id) for e in without["edges"])

    with_edges = client.get(
        f"/api/v1/works/{base.id}/reference-graph?include_ref_edges=true",
        headers=auth_headers("editor"),
    ).json()
    assert any(e["source"] != str(base.id) for e in with_edges["edges"])  # an A→B ref edge appeared
