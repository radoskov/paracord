"""DELETE /works/{id}/metadata/{assertion_id} (Phase L, item 8).

Covers: deleting an assertion removes it; deleting the canonical one re-resolves a sane canonical
from the remaining assertions (and promotes it for promotable fields), or leaves none when the field
is emptied; 404 for a missing/cross-work assertion; 403 for a non-modifying contributor.
"""

import uuid
from datetime import UTC, datetime, timedelta

from app.models.metadata import MetadataAssertion
from app.models.work import Work
from app.services.auth import create_user_session
from sqlalchemy import select


def _headers(db, user):
    token, _ = create_user_session(db, user, ttl_minutes=60)
    db.commit()
    return {"Authorization": f"Bearer {token}"}


def _work(db, *, title="w", created_by=None):
    work = Work(canonical_title=title, created_by_user_id=created_by)
    db.add(work)
    db.commit()
    db.refresh(work)
    return work


def _assertion(
    db, work, *, field="title", value="V", source="crossref", canonical=False, retrieved_at=None
):
    a = MetadataAssertion(
        entity_type="work",
        entity_id=work.id,
        field_name=field,
        value=value,
        source=source,
        selected_as_canonical=canonical,
    )
    if retrieved_at is not None:
        a.retrieved_at = retrieved_at
    db.add(a)
    db.commit()
    db.refresh(a)
    return a


def test_delete_removes_a_non_canonical_assertion(client, db, make_user):
    editor = make_user("md-ed", role="editor")
    work = _work(db)
    keep_id = _assertion(db, work, value="Keep", canonical=True).id
    work_id = work.id
    drop_id = _assertion(db, work, value="Drop", canonical=False).id

    r = client.delete(f"/api/v1/works/{work_id}/metadata/{drop_id}", headers=_headers(db, editor))
    assert r.status_code == 200
    db.expunge_all()  # clear identity map so reads hit the committed DB state
    assert db.get(MetadataAssertion, drop_id) is None
    # The canonical one is untouched.
    assert db.get(MetadataAssertion, keep_id).selected_as_canonical is True


def test_delete_canonical_repicks_remaining_and_promotes(client, db, make_user):
    editor = make_user("md-ed2", role="editor")
    work = _work(db, title="Original")
    # Two title assertions with distinct retrieved_at; the later one ("Second") is re-picked.
    now = datetime.now(UTC)
    _assertion(db, work, field="title", value="First", retrieved_at=now - timedelta(hours=2))
    _assertion(db, work, field="title", value="Second", retrieved_at=now - timedelta(hours=1))
    canonical_id = _assertion(
        db, work, field="title", value="Canonical", canonical=True, retrieved_at=now
    ).id
    work.canonical_title = "Canonical"
    db.commit()
    work_id = work.id

    r = client.delete(
        f"/api/v1/works/{work_id}/metadata/{canonical_id}", headers=_headers(db, editor)
    )
    assert r.status_code == 200
    db.expunge_all()
    # Newest remaining ("Second") becomes canonical and is promoted onto the Work (title field).
    repicked = db.scalar(
        select(MetadataAssertion).where(
            MetadataAssertion.entity_id == work_id,
            MetadataAssertion.selected_as_canonical.is_(True),
        )
    )
    assert repicked is not None and repicked.value == "Second"
    assert r.json()["canonical_title"] == "Second"


def test_delete_last_assertion_clears_canonical_keeps_work_value(client, db, make_user):
    editor = make_user("md-ed3", role="editor")
    work = _work(db, title="Kept Title")
    only_id = _assertion(db, work, field="title", value="Kept Title", canonical=True).id
    work_id = work.id

    r = client.delete(f"/api/v1/works/{work_id}/metadata/{only_id}", headers=_headers(db, editor))
    assert r.status_code == 200
    db.expunge_all()
    # No assertions left → nothing canonical; the Work's column value is preserved (not blanked).
    remaining = db.scalars(
        select(MetadataAssertion).where(MetadataAssertion.entity_id == work_id)
    ).all()
    assert remaining == []
    assert r.json()["canonical_title"] == "Kept Title"


def test_delete_404_when_assertion_missing(client, db, make_user):
    editor = make_user("md-ed4", role="editor")
    work = _work(db)
    r = client.delete(
        f"/api/v1/works/{work.id}/metadata/{uuid.uuid4()}", headers=_headers(db, editor)
    )
    assert r.status_code == 404


def test_delete_404_when_assertion_belongs_to_other_work(client, db, make_user):
    editor = make_user("md-ed5", role="editor")
    work_a = _work(db, title="A")
    work_b = _work(db, title="B")
    a = _assertion(db, work_b, value="B-val")
    # Assertion belongs to work_b, not work_a → 404 (cross-work).
    r = client.delete(f"/api/v1/works/{work_a.id}/metadata/{a.id}", headers=_headers(db, editor))
    assert r.status_code == 404
    assert db.get(MetadataAssertion, a.id) is not None


def test_delete_403_for_non_modifying_contributor(client, db, make_user):
    c1 = make_user("md-c1", role="contributor")
    c2 = make_user("md-c2", role="contributor")
    work = _work(db, title="c1 paper", created_by=c1.id)
    a = _assertion(db, work, value="V")
    r = client.delete(f"/api/v1/works/{work.id}/metadata/{a.id}", headers=_headers(db, c2))
    assert r.status_code == 403
    assert db.get(MetadataAssertion, a.id) is not None


def test_bulk_apply_best_metadata_prefers_grobid(client, db, make_user):
    """Issue 3: bulk apply promotes the GROBID value per selected paper (else first available),
    skips papers with the field locked or with no assertion, and applies to the Work."""
    editor = make_user("md-bulk", role="editor")
    # Paper 1: has both grobid + crossref titles → grobid wins.
    w1 = _work(db, title="stub1")
    _assertion(db, w1, field="title", value="Crossref Title", source="crossref")
    _assertion(db, w1, field="title", value="GROBID Title", source="grobid")
    # Paper 2: only crossref → first available wins.
    w2 = _work(db, title="stub2")
    _assertion(db, w2, field="title", value="Only Crossref", source="crossref")
    # Paper 3: title locked (user-confirmed) → skipped.
    w3 = _work(db, title="Kept Title")
    w3.confirmed_fields = ["title"]
    _assertion(db, w3, field="title", value="Should Not Win", source="grobid")
    db.commit()

    r = client.post(
        "/api/v1/works/bulk-apply-metadata",
        headers=_headers(db, editor),
        json={"work_ids": [str(w1.id), str(w2.id), str(w3.id)], "field_name": "title"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["applied"] == 2 and body["skipped"] == 1
    db.expunge_all()
    assert db.get(Work, w1.id).canonical_title == "GROBID Title"
    assert db.get(Work, w2.id).canonical_title == "Only Crossref"
    assert db.get(Work, w3.id).canonical_title == "Kept Title"  # locked, untouched


def test_bulk_apply_rejects_non_promotable_field(client, db, make_user):
    editor = make_user("md-bulk2", role="editor")
    w = _work(db)
    r = client.post(
        "/api/v1/works/bulk-apply-metadata",
        headers=_headers(db, editor),
        json={"work_ids": [str(w.id)], "field_name": "keywords"},
    )
    assert r.status_code == 400


def test_set_metadata_value_creates_locked_user_assertion(client, db, make_user):
    """Batch10 #4: setting authors (no Work column) writes a user-sourced canonical assertion,
    locks the field, and shows up in the metadata review."""
    editor = make_user("md-set", role="editor")
    w = _work(db, title="w")
    r = client.post(
        f"/api/v1/works/{w.id}/metadata/set",
        headers=_headers(db, editor),
        json={"field_name": "authors", "value": "Smith, J.; Doe, A."},
    )
    assert r.status_code == 200
    reviews = {f["field_name"]: f for f in r.json()}
    assert "authors" in reviews
    assert reviews["authors"]["canonical_value"] == "Smith, J.; Doe, A."
    assert reviews["authors"]["confirmed"] is True
    rows = db.scalars(
        select(MetadataAssertion).where(
            MetadataAssertion.entity_id == w.id, MetadataAssertion.field_name == "authors"
        )
    ).all()
    assert len(rows) == 1 and rows[0].source == "user" and rows[0].selected_as_canonical
    db.expunge_all()
    assert "authors" in (db.get(Work, w.id).confirmed_fields or [])


def test_set_metadata_value_promotable_field_updates_work(client, db, make_user):
    """A promotable field set manually also writes the Work column (verified via the API)."""
    editor = make_user("md-set2", role="editor")
    headers = _headers(db, editor)
    w = _work(db, title="Old Title")
    r = client.post(
        f"/api/v1/works/{w.id}/metadata/set",
        headers=headers,
        json={"field_name": "title", "value": "New Manual Title"},
    )
    assert r.status_code == 200
    got = client.get(f"/api/v1/works/{w.id}", headers=headers)
    assert got.status_code == 200 and got.json()["canonical_title"] == "New Manual Title"


def test_set_metadata_value_empty_clears_and_unlocks(client, db, make_user):
    """An empty value de-canonicalises existing assertions and unlocks the field."""
    editor = make_user("md-set3", role="editor")
    w = _work(db)
    _assertion(db, w, field="authors", value="A. Uthor", source="grobid", canonical=True)
    w.confirmed_fields = ["authors"]
    db.commit()
    r = client.post(
        f"/api/v1/works/{w.id}/metadata/set",
        headers=_headers(db, editor),
        json={"field_name": "authors", "value": ""},
    )
    assert r.status_code == 200
    reviews = {f["field_name"]: f for f in r.json()}
    assert reviews["authors"]["canonical_value"] is None
    assert reviews["authors"]["confirmed"] is False


def test_bulk_apply_all_promotes_every_field(client, db, make_user):
    """Issue (batch10 #3): field_name="all" promotes the best assertion for every promotable field,
    skips locked fields per-paper, and counts a paper applied if at least one field was set."""
    editor = make_user("md-bulk-all", role="editor")
    # Paper 1: assertions for several fields; venue is locked → title/abstract/year/doi apply.
    w1 = _work(db, title="stub1")
    _assertion(db, w1, field="title", value="Best Title", source="grobid")
    _assertion(db, w1, field="abstract", value="Best Abstract", source="grobid")
    _assertion(db, w1, field="year", value="2021", source="crossref")
    _assertion(db, w1, field="doi", value="10.1/x", source="crossref")
    _assertion(db, w1, field="venue", value="Should Not Win", source="grobid")
    w1.confirmed_fields = ["venue"]
    w1.venue = "Locked Venue"
    # Paper 2: no assertions at all → skipped (nothing applied).
    w2 = _work(db, title="stub2")
    db.commit()

    r = client.post(
        "/api/v1/works/bulk-apply-metadata",
        headers=_headers(db, editor),
        json={"work_ids": [str(w1.id), str(w2.id)], "field_name": "all"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["field_name"] == "all"
    assert body["applied"] == 1 and body["skipped"] == 1
    db.expunge_all()
    got = db.get(Work, w1.id)
    assert got.canonical_title == "Best Title"
    assert got.abstract == "Best Abstract"
    assert got.year == 2021
    assert got.doi == "10.1/x"
    assert got.venue == "Locked Venue"  # locked field untouched
