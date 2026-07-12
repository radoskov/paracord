"""F3a — local-reference-resolution repair.

The matcher (``reference_matching``) is the single, persisted source of truth for a reference's
``resolution_status``/``resolved_work_id``. These tests pin the two behavioural changes:

1. ``build_citation_graph`` is a pure read — it never mutates ``resolution_status`` (the dead
   read-path write is gone).
2. Deleting a cited work re-resolves the references that pointed at it, so the stored resolution
   stays accurate (they become ``external`` again, or re-link to a duplicate) instead of being left
   stale/blank until the next rescan.
"""

from app.models.citation import Reference
from app.models.work import Work
from app.services.citation_graph import build_citation_graph
from app.utils.normalization import normalize_title


def _work(db, title, **fields) -> Work:
    work = Work(canonical_title=title, normalized_title=normalize_title(title or ""), **fields)
    db.add(work)
    db.flush()
    return work


def test_build_citation_graph_does_not_mutate_resolution_status(db, make_reference) -> None:
    target = _work(db, "Shared Target", doi="10.9/target")
    citing = _work(db, "Citing Paper")
    # An UNRESOLVED reference whose identifier matches a local work in scope: the graph will resolve
    # it in-memory to build the edge, but must NOT write the result back onto the row.
    ref = make_reference(
        db,
        citing_work_id=citing.id,
        title="Shared Target",
        normalized_title=normalize_title("Shared Target"),
        doi="10.9/target",
        resolved_work_id=None,
        resolution_status="unresolved",
    )

    build_citation_graph(db, scope_type="library")

    # Same identity-mapped object the graph iterated — a mutation would show here without a refresh.
    assert ref.resolution_status == "unresolved"
    assert ref.resolved_work_id is None


def test_deleting_cited_work_reresolves_referencing_rows(client, auth_headers, db, make_reference) -> None:
    headers = auth_headers("editor")
    target = _work(db, "Cited Target", doi="10.7/target")
    citing = _work(db, "Citer")
    ref = make_reference(
        db,
        citing_work_id=citing.id,
        title="Cited Target",
        normalized_title=normalize_title("Cited Target"),
        doi="10.7/target",
        resolved_work_id=target.id,
        resolution_status="local_match",
    )
    db.commit()
    ref_id = ref.id

    resp = client.delete(f"/api/v1/works/{target.id}", headers=headers)
    assert resp.status_code == 204

    db.expire_all()
    reloaded = db.get(Reference, ref_id)
    assert reloaded is not None, "reference is still cited by another work → must not be orphan-pruned"
    assert reloaded.resolved_work_id is None
    # No other local work carries this DOI/title now, so it re-resolves to external (not left stale).
    assert reloaded.resolution_status == "external"
