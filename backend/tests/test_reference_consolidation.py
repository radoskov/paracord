"""Canonical-reference consolidation (S13/S14): auto-fold, contradiction parking, admin review."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.models.citation import CitationMention, Reference, ReferenceCitation
from app.models.work import Work
from app.services.reference_consolidation import (
    CONFLICT_MARKER,
    consolidate_references,
    list_conflicts,
    resolve_conflict,
)
from app.utils.normalization import normalize_title
from sqlalchemy import select


def _work(db, title="W", **kw) -> Work:
    work = Work(canonical_title=title, normalized_title=normalize_title(title), **kw)
    db.add(work)
    db.flush()
    return work


_SEQ = {"n": 0}


def _ref(db, title=None, created_offset=0, **kw) -> Reference:
    from app.services.reference_links import reference_dedup_key

    _SEQ["n"] += 1
    nt = normalize_title(title) if title else None
    ref = Reference(
        title=title,
        normalized_title=nt,
        dedup_key=kw.pop("dedup_key", None)
        or reference_dedup_key(
            doi=kw.get("doi"), arxiv_id=kw.get("arxiv_id"), normalized_title=nt, year=kw.get("year")
        ),
        created_at=datetime.now(UTC) + timedelta(seconds=_SEQ["n"] + created_offset),
        **kw,
    )
    db.add(ref)
    db.flush()
    return ref


def _link(db, ref, work) -> None:
    db.add(ReferenceCitation(reference_id=ref.id, citing_work_id=work.id))
    db.flush()


def test_conflict_free_twins_fold_into_oldest(db) -> None:
    citer_a, citer_b = _work(db, "Citer A"), _work(db, "Citer B")
    older = _ref(db, title="Same Paper", doi="10.1/same", resolution_status="external")
    newer = _ref(db, title="Same Paper", doi="10.1/same", resolution_status="unresolved", year=2020)
    _link(db, older, citer_a)
    _link(db, newer, citer_a)  # same citer on both twins → one link survives
    _link(db, newer, citer_b)
    db.add(CitationMention(citing_work_id=citer_b.id, reference_id=newer.id))
    db.flush()

    result = consolidate_references(db)
    db.commit()
    assert result.folded == 1 and result.conflicts == 0

    remaining = db.scalars(select(Reference)).all()
    assert [r.id for r in remaining] == [older.id]
    assert older.year == 2020  # metadata merged non-null-first
    citers = set(
        db.scalars(
            select(ReferenceCitation.citing_work_id).where(
                ReferenceCitation.reference_id == older.id
            )
        ).all()
    )
    assert citers == {citer_a.id, citer_b.id}  # deduped + repointed
    mention = db.scalar(select(CitationMention))
    assert mention.reference_id == older.id


def test_ladder_keeps_the_most_resolved_state(db) -> None:
    target = _work(db, "Target")
    _ref(db, title="Ladder Paper", doi="10.1/ladder", resolution_status="external")
    _ref(
        db,
        title="Ladder Paper",
        doi="10.1/ladder",
        resolution_status="confirmed_match",
        resolved_work_id=target.id,
    )
    result = consolidate_references(db)
    db.commit()
    assert result.folded == 1
    survivor = db.scalar(select(Reference))
    assert survivor.resolution_status == "confirmed_match"
    assert survivor.resolved_work_id == target.id


def test_legacy_arxiv_doi_key_folds_with_arxiv_id_twin(db) -> None:
    """A pre-bridge row keyed doi:10.48550/... groups with its arxiv:<base> twin."""
    legacy = _ref(
        db,
        title="Preprint",
        doi="10.48550/arxiv.2101.00001",
        dedup_key="doi:10.48550/arxiv.2101.00001",
        resolution_status="external",
    )
    _ref(db, title="Preprint", arxiv_id="2101.00001", resolution_status="external")
    result = consolidate_references(db)
    db.commit()
    assert result.folded == 1
    survivor = db.scalar(select(Reference))
    assert survivor.id == legacy.id
    assert survivor.dedup_key == "arxiv:2101.00001"  # refreshed to the canonical shape


def test_contradiction_is_parked_not_folded(db) -> None:
    work_x, work_y = _work(db, "X"), _work(db, "Y")
    a = _ref(
        db,
        title="Contested",
        doi="10.1/contested",
        resolution_status="confirmed_match",
        resolved_work_id=work_x.id,
    )
    b = _ref(
        db,
        title="Contested",
        doi="10.1/contested",
        resolution_status="confirmed_match",
        resolved_work_id=work_y.id,
    )
    result = consolidate_references(db)
    db.commit()
    assert result.folded == 0 and result.conflicts == 1
    db.refresh(a), db.refresh(b)
    assert CONFLICT_MARKER not in a.dedup_key  # oldest keeps the canonical key
    assert CONFLICT_MARKER in b.dedup_key
    # Idempotent: a rerun re-reports but does not re-park or fold.
    again = consolidate_references(db)
    assert again.folded == 0 and again.conflicts == 1


def test_confirm_vs_rejection_of_same_work_is_a_contradiction(db) -> None:
    work_x = _work(db, "X")
    _ref(
        db,
        title="Contested2",
        doi="10.1/contested2",
        resolution_status="confirmed_match",
        resolved_work_id=work_x.id,
    )
    _ref(
        db,
        title="Contested2",
        doi="10.1/contested2",
        resolution_status="rejected_match",
        suggested_work_id=work_x.id,
    )
    result = consolidate_references(db)
    assert result.conflicts == 1 and result.folded == 0


def test_rejection_of_a_different_candidate_auto_folds(db) -> None:
    work_x, work_y = _work(db, "X"), _work(db, "Y")
    _ref(
        db,
        title="Fine",
        doi="10.1/fine",
        resolution_status="confirmed_match",
        resolved_work_id=work_x.id,
    )
    _ref(
        db,
        title="Fine",
        doi="10.1/fine",
        resolution_status="rejected_match",
        suggested_work_id=work_y.id,  # rejected a DIFFERENT candidate — no contradiction
    )
    result = consolidate_references(db)
    assert result.folded == 1 and result.conflicts == 0
    survivor = db.scalar(select(Reference))
    assert survivor.resolution_status == "confirmed_match"


def test_signal_less_rows_are_exempt(db) -> None:
    db.add_all([Reference(resolution_status="resolved") for _ in range(2)])
    db.flush()
    result = consolidate_references(db)
    assert result.groups_scanned == 0 and result.folded == 0


def test_list_and_resolve_conflict(db) -> None:
    work_x, work_y = _work(db, "X"), _work(db, "Y")
    citer = _work(db, "Citer")
    a = _ref(
        db,
        title="Contested",
        doi="10.1/contested",
        resolution_status="confirmed_match",
        resolved_work_id=work_x.id,
    )
    b = _ref(
        db,
        title="Contested",
        doi="10.1/contested",
        resolution_status="confirmed_match",
        resolved_work_id=work_y.id,
    )
    _link(db, a, citer)
    _link(db, b, citer)
    consolidate_references(db)
    db.commit()

    groups = list_conflicts(db)
    assert len(groups) == 1
    entries = groups[0]["references"]
    assert {e["resolved_work_title"] for e in entries} == {"X", "Y"}
    assert all(e["citing_count"] == 1 for e in entries)

    # Admin picks B's resolution (work Y) — group folds into the OLDEST row with Y's state.
    removed = resolve_conflict(db, winner_reference_id=b.id)
    db.commit()
    assert removed == 1
    survivor = db.scalar(select(Reference))
    assert survivor.id == a.id  # oldest survives...
    assert survivor.resolved_work_id == work_y.id  # ...with the chosen resolution
    assert CONFLICT_MARKER not in survivor.dedup_key
    assert list_conflicts(db) == []


def test_resolve_requires_a_pending_conflict(db) -> None:
    from app.errors import ConflictError, NotFoundError

    lonely = _ref(db, title="Lonely", doi="10.1/lonely", resolution_status="external")
    with pytest.raises(ConflictError):
        resolve_conflict(db, winner_reference_id=lonely.id)
    with pytest.raises(NotFoundError):
        resolve_conflict(db, winner_reference_id=uuid.uuid4())


# --- admin endpoints ---------------------------------------------------------------------------


def _seed_conflict(db) -> tuple:
    work_x, work_y = _work(db, "X"), _work(db, "Y")
    a = _ref(
        db,
        title="Contested",
        doi="10.1/contested",
        resolution_status="confirmed_match",
        resolved_work_id=work_x.id,
    )
    b = _ref(
        db,
        title="Contested",
        doi="10.1/contested",
        resolution_status="confirmed_match",
        resolved_work_id=work_y.id,
    )
    db.commit()
    return a, b


def test_scan_endpoint_runs_inline_without_queue(client, auth_headers, db, monkeypatch) -> None:
    from app.workers import queue as queue_mod

    monkeypatch.setattr(queue_mod, "enqueue_reference_consolidation", lambda: None)
    _seed_conflict(db)
    resp = client.post("/api/v1/admin/reference-dupes/scan", headers=auth_headers("owner"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["queued"] is False
    assert body["result"]["conflicts"] == 1 and body["result"]["folded"] == 0

    got = client.get("/api/v1/admin/reference-dupes", headers=auth_headers("owner")).json()
    assert got["last_scan"]["conflicts"] == 1
    assert len(got["conflicts"]) == 1
    assert len(got["conflicts"][0]["references"]) == 2


def test_scan_endpoint_enqueues_when_queue_available(client, auth_headers, monkeypatch) -> None:
    from app.workers import queue as queue_mod

    monkeypatch.setattr(
        queue_mod, "enqueue_reference_consolidation", lambda: "reference-consolidation"
    )
    resp = client.post("/api/v1/admin/reference-dupes/scan", headers=auth_headers("owner"))
    assert resp.status_code == 200
    assert resp.json() == {
        "queued": True,
        "job_id": "reference-consolidation",
        "result": None,
    }


def test_resolve_endpoint_folds_group(client, auth_headers, db, monkeypatch) -> None:
    from app.workers import queue as queue_mod

    monkeypatch.setattr(queue_mod, "enqueue_reference_consolidation", lambda: None)
    a, b = _seed_conflict(db)
    client.post("/api/v1/admin/reference-dupes/scan", headers=auth_headers("owner"))
    resp = client.post(
        "/api/v1/admin/reference-dupes/resolve",
        headers=auth_headers("owner"),
        json={"winner_reference_id": str(b.id)},
    )
    assert resp.status_code == 200
    assert resp.json()["conflicts"] == []
    survivor = db.scalar(select(Reference))
    assert survivor.id == a.id and str(survivor.resolved_work_id) is not None


def test_reference_dupes_requires_admin(client, auth_headers) -> None:
    assert (
        client.get("/api/v1/admin/reference-dupes", headers=auth_headers("reader")).status_code
        == 403
    )
    assert (
        client.post(
            "/api/v1/admin/reference-dupes/scan", headers=auth_headers("editor")
        ).status_code
        == 403
    )


def test_fold_survives_postgres_fk_cascade_semantics(db) -> None:
    """Regression (real-PC failure): with FK enforcement ON (as on Postgres), deleting the loser
    references must not cascade away link rows the session still has pending — repointed links
    have to be flushed first, or the fold dies with StaleDataError."""
    from sqlalchemy import text

    db.execute(text("PRAGMA foreign_keys=ON"))  # make SQLite behave like Postgres here
    try:
        citer_a, citer_b = _work(db, "Citer A"), _work(db, "Citer B")
        older = _ref(db, title="Cascade Paper", doi="10.1/cascade", resolution_status="external")
        newer = _ref(db, title="Cascade Paper", doi="10.1/cascade", resolution_status="unresolved")
        _link(db, older, citer_a)
        _link(db, newer, citer_a)  # duplicate link → ORM delete path
        _link(db, newer, citer_b)  # repointed link → ORM update path (the row that cascaded away)
        db.add(CitationMention(citing_work_id=citer_b.id, reference_id=newer.id))
        db.commit()

        result = consolidate_references(db)
        db.commit()
        assert result.folded == 1
        citers = set(
            db.scalars(
                select(ReferenceCitation.citing_work_id).where(
                    ReferenceCitation.reference_id == older.id
                )
            ).all()
        )
        assert citers == {citer_a.id, citer_b.id}
        assert db.scalar(select(CitationMention)).reference_id == older.id
    finally:
        db.execute(text("PRAGMA foreign_keys=OFF"))
