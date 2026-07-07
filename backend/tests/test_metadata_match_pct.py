"""GET /works/{id}/metadata match_pct (Batch P, P2).

A conflicting field carries a 0-100 similarity so the UI can show how alike two values are.
Values that differ only by formatting (line-break hyphenation, whitespace, case) score ~100;
genuinely different values score low; a field with no conflict has match_pct = None.
"""

from app.models.metadata import MetadataAssertion
from app.models.work import Work
from app.services.auth import create_user_session
from app.utils.normalization import normalize_for_similarity, similarity_pct


def _headers(db, user):
    token, _ = create_user_session(db, user, ttl_minutes=60)
    db.commit()
    return {"Authorization": f"Bearer {token}"}


def _work(db, *, title="w"):
    work = Work(canonical_title=title)
    db.add(work)
    db.commit()
    db.refresh(work)
    return work


def _assertion(db, work, *, field, value, source, canonical=False):
    a = MetadataAssertion(
        entity_type="work",
        entity_id=work.id,
        field_name=field,
        value=value,
        source=source,
        selected_as_canonical=canonical,
    )
    db.add(a)
    db.commit()
    return a


def _review(payload, field_name):
    return next(f for f in payload if f["field_name"] == field_name)


def test_normalize_joins_hyphenated_line_breaks_and_whitespace():
    assert normalize_for_similarity("infor-\nmation  systems") == "information systems"
    assert normalize_for_similarity("Hello\n\tWorld") == "hello world"


def test_similarity_pct_identical_modulo_formatting_is_100():
    a = "We study infor-\nmation retrieval systems."
    b = "We study information retrieval systems."
    assert similarity_pct(a, b) == 100.0


def test_metadata_match_pct_high_for_formatting_only_conflict(client, db, make_user):
    editor = make_user("mp-ed1", role="editor")
    work = _work(db)
    # Two abstracts differing only by end-of-line hyphenation + whitespace.
    _assertion(
        db,
        work,
        field="abstract",
        value="Neural nets learn represen-\ntations from   data.",
        source="crossref",
        canonical=True,
    )
    _assertion(
        db,
        work,
        field="abstract",
        value="Neural nets learn representations from data.",
        source="openalex",
    )

    r = client.get(f"/api/v1/works/{work.id}/metadata", headers=_headers(db, editor))
    assert r.status_code == 200
    review = _review(r.json(), "abstract")
    assert review["has_conflict"] is True
    assert review["match_pct"] == 100.0


def test_metadata_match_pct_low_for_genuinely_different_values(client, db, make_user):
    editor = make_user("mp-ed2", role="editor")
    work = _work(db)
    _assertion(
        db,
        work,
        field="title",
        value="Attention Is All You Need",
        source="crossref",
        canonical=True,
    )
    _assertion(db, work, field="title", value="A Survey of Photosynthesis", source="openalex")

    r = client.get(f"/api/v1/works/{work.id}/metadata", headers=_headers(db, editor))
    review = _review(r.json(), "title")
    assert review["has_conflict"] is True
    assert 0.0 <= review["match_pct"] < 60.0


def test_metadata_match_pct_none_without_conflict(client, db, make_user):
    editor = make_user("mp-ed3", role="editor")
    work = _work(db)
    _assertion(db, work, field="venue", value="NeurIPS", source="crossref", canonical=True)
    _assertion(db, work, field="venue", value="NeurIPS", source="openalex")

    r = client.get(f"/api/v1/works/{work.id}/metadata", headers=_headers(db, editor))
    review = _review(r.json(), "venue")
    assert review["has_conflict"] is False
    assert review["match_pct"] is None
