"""Regression: WorkRead tolerates NULL confirmed_fields/keywords (pre-migration rows).

A NULL in these JSONB columns previously failed WorkRead validation, 500-ing the whole
``GET /works`` list (which surfaces in the browser as a CORS "NetworkError").
"""

from app.models.work import Work
from sqlalchemy import update


def test_works_list_with_null_jsonb_columns(client, auth_headers, db):
    wid = client.post(
        "/api/v1/works", headers=auth_headers("editor"), json={"canonical_title": "Legacy row"}
    ).json()["id"]
    # Simulate a pre-migration row: columns added nullable, never backfilled.
    import uuid

    db.execute(
        update(Work).where(Work.id == uuid.UUID(wid)).values(confirmed_fields=None, keywords=None)
    )
    db.commit()

    listed = client.get("/api/v1/works", headers=auth_headers("reader"))
    assert listed.status_code == 200
    row = next(w for w in listed.json()["items"] if w["id"] == wid)
    assert row["confirmed_fields"] == []
    assert row["keywords"] == []

    one = client.get(f"/api/v1/works/{wid}", headers=auth_headers("reader"))
    assert one.status_code == 200
    assert one.json()["keywords"] == []


def test_work_read_exposes_citation_count(client, auth_headers, db):
    """WorkRead surfaces the citation-count snapshot (Track C P1); NULL by default, then set."""
    import uuid
    from datetime import UTC, datetime

    wid = client.post(
        "/api/v1/works", headers=auth_headers("editor"), json={"canonical_title": "Cited paper"}
    ).json()["id"]

    fresh = client.get(f"/api/v1/works/{wid}", headers=auth_headers("reader")).json()
    assert fresh["citation_count"] is None
    assert fresh["citation_count_source"] is None
    assert fresh["citation_count_fetched_at"] is None

    db.execute(
        update(Work)
        .where(Work.id == uuid.UUID(wid))
        .values(
            citation_count=42,
            citation_count_source="openalex",
            citation_count_fetched_at=datetime.now(UTC),
        )
    )
    db.commit()

    updated = client.get(f"/api/v1/works/{wid}", headers=auth_headers("reader")).json()
    assert updated["citation_count"] == 42
    assert updated["citation_count_source"] == "openalex"
    assert updated["citation_count_fetched_at"] is not None
