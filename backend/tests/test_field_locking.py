"""Per-field user_confirmed locking (SPEC §8.12)."""

from app.models.metadata import MetadataAssertion
from app.models.work import Work
from app.services.metadata_enrichment import ExternalMetadata, _store_external
from sqlalchemy import select


def test_editing_a_field_locks_it(client, auth_headers):
    h = auth_headers("editor")
    wid = client.post("/api/v1/works", headers=h, json={"canonical_title": "T"}).json()["id"]
    updated = client.patch(
        f"/api/v1/works/{wid}", headers=h, json={"venue": "NeurIPS", "year": 2021}
    ).json()
    assert set(updated["confirmed_fields"]) == {"venue", "year"}


def test_confirm_endpoint_toggles(client, auth_headers):
    h = auth_headers("editor")
    wid = client.post("/api/v1/works", headers=h, json={"canonical_title": "T"}).json()["id"]
    on = client.post(
        f"/api/v1/works/{wid}/metadata/confirm", headers=h, json={"field_name": "title"}
    ).json()
    assert "title" in on["confirmed_fields"]
    off = client.post(
        f"/api/v1/works/{wid}/metadata/confirm",
        headers=h,
        json={"field_name": "title", "confirmed": False},
    ).json()
    assert "title" not in off["confirmed_fields"]


def test_enrichment_does_not_overwrite_a_confirmed_field(db):
    """A confirmed field is not promoted to canonical by an external assertion."""
    work = Work(
        canonical_title="My Title",
        normalized_title="my title",
        venue="My Venue",
        confirmed_fields=["venue"],
    )
    db.add(work)
    db.flush()
    _store_external(
        db,
        work,
        ExternalMetadata(source="crossref", title="Other Title", venue="Other Venue"),
    )
    db.flush()
    # venue is locked → stays; title is unlocked → promoted from the trusted source.
    assert work.venue == "My Venue"
    assert work.canonical_title == "Other Title"
    # The external assertion is still recorded for review.
    assert (
        db.scalar(
            select(MetadataAssertion).where(
                MetadataAssertion.entity_id == work.id, MetadataAssertion.field_name == "venue"
            )
        )
        is not None
    )
