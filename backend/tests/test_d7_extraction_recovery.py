"""D7 — enqueue visibility, queue health, and idempotent owed-extraction recovery.

Covers the three D7 deliverables and the two owner-required invariants:
  * ``extraction_queued`` surfaces False when the enqueue is dropped (Redis down);
  * the queue-health endpoint reports ``redis_reachable``/``worker_count``/``queued`` and degrades
    to ``redis_reachable=False`` without raising;
  * the recovery sweep re-enqueues owed files and NEVER touches never-requested (marker-null)
    files — invariant 1;
  * the deterministic job id makes a double enqueue a single job — invariant 2;
  * the extraction worker clears the owed marker on terminal success AND terminal failure.

The two Redis-dependent tests skip automatically when no Redis is reachable (mirrors the migration
parity test), so the SQLite-only local run stays green; they run in the compose api container.
"""

import uuid
from datetime import UTC, datetime

import fitz
import pytest
from app.core.config import get_settings
from app.models.file import File
from app.workers import queue as queue_mod
from app.workers import recovery


def _real_pdf_bytes() -> bytes:
    """A real, openable single-page PDF (the AUDIT E2 upload probe rejects header-only stubs)."""
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "d7 test fixture")
    data = doc.tobytes()
    doc.close()
    return data


_PDF_BYTES = _real_pdf_bytes()


def _redis_up() -> bool:
    try:
        from redis import Redis

        Redis.from_url(get_settings().redis_url).ping()
        return True
    except Exception:  # noqa: BLE001 - absence is the thing under test elsewhere
        return False


# --- Part 1: extraction_queued surfacing -----------------------------------


def test_upload_surfaces_extraction_queued_false_when_enqueue_dropped(
    client, auth_headers, db, monkeypatch
) -> None:
    """A dropped enqueue (Redis down) surfaces extraction_queued=False AND leaves the owed marker."""
    monkeypatch.setattr("app.api.v1.endpoints.imports.enqueue_extraction", lambda _file_id: None)
    resp = client.post(
        "/api/v1/imports/upload",
        headers=auth_headers("editor"),
        files={"file": ("paper.pdf", _PDF_BYTES, "application/pdf")},
    )
    assert resp.status_code == 201
    assert resp.json()["extraction_queued"] is False
    # The file keeps its durable owed marker so the recovery sweep can retry it (D7).
    file = db.query(File).one()
    assert file.extraction_requested_at is not None


def test_upload_surfaces_extraction_queued_true_when_enqueued(
    client, auth_headers, monkeypatch
) -> None:
    monkeypatch.setattr(
        "app.api.v1.endpoints.imports.enqueue_extraction", lambda _file_id: "extract:x"
    )
    resp = client.post(
        "/api/v1/imports/upload",
        headers=auth_headers("editor"),
        files={"file": ("paper.pdf", _PDF_BYTES, "application/pdf")},
    )
    assert resp.status_code == 201
    assert resp.json()["extraction_queued"] is True


def test_agent_extract_endpoint_reports_extraction_queued(
    client, auth_headers, db, monkeypatch
) -> None:
    """The work-level re-extract trigger persists the owed marker before enqueue (D7)."""
    monkeypatch.setattr("app.api.v1.endpoints.works.enqueue_extraction", lambda _file_id: "job-1")
    h = auth_headers("editor")
    work = client.post("/api/v1/works", headers=h, json={"canonical_title": "Has a PDF"}).json()
    client.post(
        f"/api/v1/works/{work['id']}/files",
        headers=h,
        files={"file": ("paper.pdf", _PDF_BYTES, "application/pdf")},
    )
    resp = client.post(f"/api/v1/works/{work['id']}/extract", headers=h)
    assert resp.status_code == 202
    assert resp.json()["status"] == "queued"
    # Every attached file is marked owed in the same commit as the enqueue.
    assert db.query(File).one().extraction_requested_at is not None


# --- Part 2: queue-health endpoint ------------------------------------------


def test_queue_status_degrades_without_raising(monkeypatch) -> None:
    """A dead Redis yields redis_reachable=False/worker_count=0/queued=0 and never raises."""
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6390/0")  # closed port
    get_settings.cache_clear()
    try:
        status = queue_mod.queue_status()
    finally:
        get_settings.cache_clear()
    assert status["redis_reachable"] is False
    assert status["worker_count"] == 0
    assert status["queued"] == 0
    assert status["available"] is False


def test_jobs_endpoint_reports_health_fields(client, auth_headers) -> None:
    """The /jobs endpoint always returns the health fields, whether Redis is up or down."""
    resp = client.get("/api/v1/jobs", headers=auth_headers("editor"))
    assert resp.status_code == 200
    body = resp.json()
    assert {"redis_reachable", "worker_count", "queued"} <= set(body)


# --- Part 3, invariant 1: only owed files are swept -------------------------


def test_owed_query_selects_only_marked_non_extracting_files(db) -> None:
    """Invariant 1: never-requested (marker-null) files are never swept; extracting ones skipped."""
    owed = File(
        sha256="a" * 64, size_bytes=1, status="available", extraction_requested_at=datetime.now(UTC)
    )
    never_requested = File(sha256="b" * 64, size_bytes=1, status="available")  # marker stays NULL
    in_flight = File(
        sha256="c" * 64,
        size_bytes=1,
        status="extracting",
        extraction_requested_at=datetime.now(UTC),
    )
    db.add_all([owed, never_requested, in_flight])
    db.commit()

    ids = set(recovery.owed_extraction_file_ids(db))
    assert owed.id in ids
    assert never_requested.id not in ids  # nobody asked to extract it
    assert in_flight.id not in ids  # already extracting


def test_sweep_reenqueues_each_owed_file(monkeypatch) -> None:
    """The sweep enqueues exactly the owed files it is handed (idempotent enqueue underneath)."""
    owed_ids = [uuid.uuid4(), uuid.uuid4()]
    enqueued: list = []
    monkeypatch.setattr(recovery, "_redis_reachable", lambda: True)
    monkeypatch.setattr(recovery, "owed_extraction_file_ids", lambda _db: owed_ids)
    monkeypatch.setattr(
        recovery, "enqueue_extraction", lambda fid: enqueued.append(fid) or f"extract:{fid}"
    )
    result = recovery.sweep_owed_extractions()
    assert enqueued == owed_ids
    assert result == {"considered": 2, "requeued": 2, "skipped": 0, "redis_reachable": True}


def test_sweep_skips_when_redis_unreachable(monkeypatch) -> None:
    """A down queue makes the sweep a no-op (it must tolerate Redis being absent)."""
    monkeypatch.setattr(recovery, "_redis_reachable", lambda: False)
    called = []
    monkeypatch.setattr(recovery, "enqueue_extraction", lambda fid: called.append(fid))
    result = recovery.sweep_owed_extractions()
    assert result["redis_reachable"] is False
    assert called == []


# --- Part 3, invariant 2: deterministic-job-id dedup ------------------------


@pytest.mark.skipif(not _redis_up(), reason="deterministic-id dedup needs a reachable Redis")
def test_double_enqueue_yields_a_single_job(monkeypatch) -> None:
    """Invariant 2: enqueuing extraction twice for one file produces exactly one queued job."""
    # Use a throwaway queue name so the live worker doesn't consume the job mid-assertion.
    monkeypatch.setattr(queue_mod, "QUEUE_NAME", f"test-d7-{uuid.uuid4().hex}")
    file_id = uuid.uuid4()
    try:
        first = queue_mod.enqueue_extraction(file_id)
        second = queue_mod.enqueue_extraction(file_id)
        assert first == second == f"extract-{file_id}"
        q = queue_mod.get_queue()
        assert q.count == 1  # one job in the queue, not two
        assert q.get_job_ids().count(first) == 1
    finally:
        import contextlib

        q = queue_mod.get_queue()
        q.empty()
        from rq.job import Job

        with contextlib.suppress(Exception):  # best-effort cleanup
            Job.fetch(f"extract-{file_id}", connection=q.connection).delete()


# --- Worker clears the marker on both terminal outcomes ---------------------


@pytest.fixture()
def worker_env(session_factory, monkeypatch):
    """Run the extraction worker against the test DB, stubbing GROBID/enrichment out."""
    monkeypatch.setattr("app.db.session.SessionLocal", session_factory)

    class _StubGrobid:
        def __init__(self, *_a, **_k) -> None:
            self.process_fulltext_document_sync = None

    monkeypatch.setattr("app.services.grobid_client.GrobidClient", _StubGrobid)
    monkeypatch.setattr("app.services.agent_files.discard_after_extract", lambda *a, **k: False)
    monkeypatch.setattr("app.workers.queue.enqueue_enrichment", lambda _w: None)
    return session_factory


def _seed_owed_file(session_factory) -> uuid.UUID:
    session = session_factory()
    file = File(
        sha256=(uuid.uuid4().hex + uuid.uuid4().hex)[:64],
        size_bytes=1,
        status="available",
        extraction_requested_at=datetime.now(UTC),
    )
    session.add(file)
    session.commit()
    fid = file.id
    session.close()
    return fid


def test_worker_clears_marker_on_success(worker_env, monkeypatch) -> None:
    from app.workers.jobs import extract_pdf_job

    monkeypatch.setattr("app.services.extraction.extract_and_store", lambda *a, **k: None)
    fid = _seed_owed_file(worker_env)
    extract_pdf_job(str(fid))
    session = worker_env()
    file = session.get(File, fid)
    assert file.status == "extracted"
    assert file.extraction_requested_at is None  # no longer owed
    session.close()


def test_worker_clears_marker_on_failure(worker_env, monkeypatch) -> None:
    from app.workers.jobs import extract_pdf_job

    def _boom(*_a, **_k):
        raise RuntimeError("grobid exploded")

    monkeypatch.setattr("app.services.extraction.extract_and_store", _boom)
    fid = _seed_owed_file(worker_env)
    with pytest.raises(RuntimeError):
        extract_pdf_job(str(fid))
    session = worker_env()
    file = session.get(File, fid)
    assert file.status == "extract_failed"
    assert file.extraction_requested_at is None  # marker means "still owed", not "ever failed"
    session.close()


def _doi_integrity_error():
    """A synthetic SQLAlchemy IntegrityError shaped like a psycopg uq_works_doi unique violation."""
    from sqlalchemy.exc import IntegrityError

    class _Diag:
        constraint_name = "uq_works_doi"
        message_detail = "Key (doi)=(10.1/dup) already exists."

    class _Orig(Exception):
        diag = _Diag()

    return IntegrityError("UPDATE works SET doi=...", {}, _Orig("duplicate key"))


def test_worker_handles_doi_conflict_on_extract(worker_env, monkeypatch) -> None:
    """Issue 6: a uq_works_doi collision during extraction is a handled terminal state — the file is
    marked failed (marker cleared so no D7 retry loop), an audit event is recorded, and the job
    raises a concise message instead of a raw SQL dump."""
    from app.models.audit import AuditEvent
    from app.workers.jobs import _DOI_CONFLICT_MESSAGE, extract_pdf_job

    def _conflict(*_a, **_k):
        raise _doi_integrity_error()

    monkeypatch.setattr("app.services.extraction.extract_and_store", _conflict)
    fid = _seed_owed_file(worker_env)
    with pytest.raises(RuntimeError, match="already belongs to another paper"):
        extract_pdf_job(str(fid))
    session = worker_env()
    file = session.get(File, fid)
    assert file.status == "extract_failed"
    assert file.extraction_requested_at is None  # terminal → not re-swept
    event = (
        session.query(AuditEvent).filter(AuditEvent.event_type == "metadata.doi_conflict").first()
    )
    assert event is not None
    assert event.details["phase"] == "extract"
    assert _DOI_CONFLICT_MESSAGE  # message constant is defined/non-empty
    session.close()


def test_non_doi_integrity_error_still_raises_raw(worker_env, monkeypatch) -> None:
    """A different constraint violation must NOT be swallowed as a DOI conflict — it surfaces."""
    from app.workers.jobs import extract_pdf_job
    from sqlalchemy.exc import IntegrityError

    class _Orig(Exception):
        diag = type("D", (), {"constraint_name": "some_other_uq", "message_detail": "x"})()

    def _conflict(*_a, **_k):
        raise IntegrityError("INSERT ...", {}, _Orig("other"))

    monkeypatch.setattr("app.services.extraction.extract_and_store", _conflict)
    fid = _seed_owed_file(worker_env)
    with pytest.raises(IntegrityError):
        extract_pdf_job(str(fid))
    session = worker_env()
    assert session.get(File, fid).status == "extract_failed"
    session.close()
