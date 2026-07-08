"""Per-paper weighted reference graph (B7 / issue_batch_6 #5)."""

from app.models.citation import CitationMention, Reference
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
    client, auth_headers, db
) -> None:
    base = Work(canonical_title="Base", normalized_title="base", year=2020)
    local_target = Work(canonical_title="Local Cited", normalized_title="local cited", year=2015)
    db.add_all([base, local_target])
    db.flush()
    ref_local = Reference(
        citing_work_id=base.id,
        resolved_work_id=local_target.id,
        title="Local Cited",
        year=2015,
        resolution_status="resolved",
    )
    ref_ext = Reference(citing_work_id=base.id, title="External Ref", year=2010)
    db.add_all([ref_local, ref_ext])
    db.flush()
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


def test_reference_graph_local_ref_to_ref_edges_opt_in(client, auth_headers, db) -> None:
    """include_ref_edges adds a citation edge between two resolved-local references when one cites
    the other."""
    base = Work(canonical_title="Base", normalized_title="base", year=2021)
    a = Work(canonical_title="A", normalized_title="a", year=2018)
    b = Work(canonical_title="B", normalized_title="b", year=2016)
    db.add_all([base, a, b])
    db.flush()
    db.add_all(
        [
            Reference(
                citing_work_id=base.id,
                resolved_work_id=a.id,
                title="A",
                resolution_status="resolved",
            ),
            Reference(
                citing_work_id=base.id,
                resolved_work_id=b.id,
                title="B",
                resolution_status="resolved",
            ),
            # A cites B (both are references of the base) → a ref→ref edge when opted in.
            Reference(
                citing_work_id=a.id, resolved_work_id=b.id, title="B", resolution_status="resolved"
            ),
        ]
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
