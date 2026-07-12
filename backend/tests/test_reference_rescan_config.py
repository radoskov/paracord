"""F3a — the reference-rescan-on-startup toggle (AppConfig).

Covers the model/service/endpoint plumbing for the owner toggle that (on startup) enqueues a full
library-wide reference→work rematch. The 6-line startup glue in ``main.lifespan`` mirrors the
already-tested D7 owed-extraction sweep and is exercised at integration time.
"""

from app.services.app_config import (
    effective_reference_rescan_on_startup,
    update_reference_rescan_on_startup,
)


def test_rescan_on_startup_defaults_off_and_persists(db) -> None:
    assert effective_reference_rescan_on_startup(db) is False  # absent row → OFF
    update_reference_rescan_on_startup(db, value=True)
    assert effective_reference_rescan_on_startup(db) is True
    update_reference_rescan_on_startup(db, value=False)
    assert effective_reference_rescan_on_startup(db) is False


def test_admin_app_config_roundtrips_rescan_toggle(client, auth_headers) -> None:
    headers = auth_headers("owner")

    got = client.get("/api/v1/admin/app-config", headers=headers).json()
    assert got["reference_rescan_on_startup"] is False

    resp = client.patch(
        "/api/v1/admin/app-config",
        headers=headers,
        json={"reference_rescan_on_startup": True},
    )
    assert resp.status_code == 200
    assert resp.json()["reference_rescan_on_startup"] is True

    # Persists on a fresh GET, and the toggle didn't disturb the other config fields.
    got2 = client.get("/api/v1/admin/app-config", headers=headers).json()
    assert got2["reference_rescan_on_startup"] is True
    assert "use_fuzzy_match_as_confirmed" in got2
    assert "max_papers_per_page" in got2


def test_app_config_rescan_toggle_requires_admin(client, auth_headers) -> None:
    resp = client.patch(
        "/api/v1/admin/app-config",
        headers=auth_headers("editor"),
        json={"reference_rescan_on_startup": True},
    )
    assert resp.status_code == 403


def test_full_rescan_job_matches_references_and_external_papers(db, monkeypatch) -> None:
    """S8: the batched, index-driven full rescan resolves both directions end-to-end."""
    import contextlib

    from app.models.citation import Reference
    from app.models.external_citation import ExternalPaper
    from app.models.work import Work
    from app.utils.normalization import normalize_title
    from app.workers import jobs

    work = Work(
        canonical_title="A Landmark Paper",
        normalized_title=normalize_title("A Landmark Paper"),
        doi="10.1/landmark",
    )
    ref = Reference(title="The Landmark Paper", doi="10.1/landmark", resolution_status="external")
    ref.normalized_title = normalize_title(ref.title)
    citer = ExternalPaper(
        dedup_key="doi:10.9/x", source="openalex", title="Landmark Paper", doi="10.1/landmark"
    )
    db.add_all([work, ref, citer])
    db.commit()

    # Point the job's SessionLocal at the test database (same engine/session factory).
    @contextlib.contextmanager
    def _session():
        yield db

    import app.db.session as db_session

    monkeypatch.setattr(db_session, "SessionLocal", lambda: _session())

    jobs.rescan_reference_matches_job()
    db.expire_all()
    assert ref.resolved_work_id == work.id
    assert ref.resolution_status == "local_match"
    assert citer.resolved_work_id == work.id
