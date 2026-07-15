"""Background-queue helper tests."""

from app.core.config import get_settings
from app.workers import queue


def test_enqueue_extraction_is_best_effort_without_redis(monkeypatch) -> None:
    """A missing/unreachable Redis must not raise — import must still succeed."""
    # Point at a closed local port so the connection is refused immediately.
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6390/0")
    get_settings.cache_clear()
    try:
        assert queue.enqueue_extraction("00000000-0000-0000-0000-000000000000") is None
    finally:
        get_settings.cache_clear()


def test_enqueue_work_jobs_are_best_effort_without_redis(monkeypatch) -> None:
    """1c: the per-work enqueue helpers also fail open (None) when Redis is unreachable."""
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6390/0")
    get_settings.cache_clear()
    wid = "00000000-0000-0000-0000-000000000000"
    try:
        assert queue.enqueue_enrichment(wid) is None
        assert queue.enqueue_embedding(wid) is None
        assert queue.enqueue_chunking(wid) is None
        assert queue.enqueue_topics(wid) is None
        assert queue.enqueue_keywords(wid) is None
    finally:
        get_settings.cache_clear()


class _FakeQueue:
    def __init__(self) -> None:
        self.connection = object()
        self.enqueued: list[tuple] = []

    def enqueue(self, func, *args, job_id=None, **kw):
        self.enqueued.append((func, args, job_id))
        return type("J", (), {"id": job_id})()


def test_enqueue_work_job_coalesces_in_flight_and_uniquifies_reruns(monkeypatch) -> None:
    """1c + UX batch: a per-work enqueue is a no-op while a job for the same work is in flight,
    but a re-run after a terminal job gets a NEW unique id (a fresh Jobs-tab entry, not an
    in-place overwrite of the finished one)."""
    wid = "11111111-1111-1111-1111-111111111111"

    fake = _FakeQueue()
    monkeypatch.setattr(queue, "get_queue", lambda: fake)
    # No live job → enqueues under a fresh unique id keeping the readable {prefix}-{work_id} stem.
    monkeypatch.setattr(queue, "_live_coalesced_job", lambda _conn, _key: None)
    first = queue.enqueue_enrichment(wid)
    assert first is not None and first.startswith(f"enrich-{wid}-")
    assert fake.enqueued[0][0] == queue.ENRICH_JOB

    second = queue.enqueue_enrichment(wid)
    assert second is not None and second.startswith(f"enrich-{wid}-")
    assert second != first  # a re-run is a NEW entry

    # A live job for the same work → skip the enqueue, return the live id.
    fake2 = _FakeQueue()
    monkeypatch.setattr(queue, "get_queue", lambda: fake2)
    monkeypatch.setattr(queue, "_live_coalesced_job", lambda _conn, key: f"{key}-live1234")
    assert queue.enqueue_keywords(wid) == f"keywords-{wid}-live1234"
    assert fake2.enqueued == []  # not enqueued a second time


def test_live_coalesced_job_reads_pointer_and_falls_back_to_bare_key(monkeypatch) -> None:
    """The latest-job pointer finds the in-flight run; a bare-key probe covers jobs enqueued
    before the unique-id scheme."""

    class _Conn:
        def __init__(self, mapping):
            self.mapping = mapping

        def get(self, k):
            return self.mapping.get(k)

    conn = _Conn({queue._LATEST_JOB_KEY_PREFIX + "chunk-w": b"chunk-w-abc12345"})
    # Pointer target is live → returned.
    monkeypatch.setattr(
        queue, "_live_job_id", lambda _c, jid: jid if jid == "chunk-w-abc12345" else None
    )
    assert queue._live_coalesced_job(conn, "chunk-w") == "chunk-w-abc12345"
    # Pointer target terminal, but a legacy job under the bare key is live → returned.
    monkeypatch.setattr(queue, "_live_job_id", lambda _c, jid: jid if jid == "chunk-w" else None)
    assert queue._live_coalesced_job(conn, "chunk-w") == "chunk-w"
    # Nothing live anywhere → None.
    monkeypatch.setattr(queue, "_live_job_id", lambda _c, _jid: None)
    assert queue._live_coalesced_job(conn, "chunk-w") is None


def test_order_jobs_newest_first_active_above_terminal_and_recent_on_top() -> None:
    """Item 9: active jobs on top, then terminal, most-recent first within each band."""
    jobs = [
        {"id": "fin-old", "status": "finished", "ended_at": "2026-01-01T00:00:00"},
        {"id": "fin-new", "status": "finished", "ended_at": "2026-06-01T00:00:00"},
        {"id": "run-old", "status": "started", "enqueued_at": "2026-02-01T00:00:00"},
        {"id": "run-new", "status": "started", "enqueued_at": "2026-05-01T00:00:00"},
        {"id": "queued", "status": "queued", "enqueued_at": "2026-04-01T00:00:00"},
        {"id": "failed-new", "status": "failed", "ended_at": "2026-07-01T00:00:00"},
    ]
    ordered = [j["id"] for j in queue._order_jobs_newest_first(list(jobs))]
    # Active band first (running/queued), newest by timestamp on top; then terminal band newest-first.
    assert ordered == ["run-new", "queued", "run-old", "failed-new", "fin-new", "fin-old"]


def test_order_jobs_newest_first_missing_timestamp_sorts_last() -> None:
    jobs = [
        {"id": "no-ts", "status": "finished"},
        {"id": "has-ts", "status": "finished", "ended_at": "2026-01-01T00:00:00"},
    ]
    ordered = [j["id"] for j in queue._order_jobs_newest_first(list(jobs))]
    assert ordered == ["has-ts", "no-ts"]


def test_enqueue_work_job_declares_transient_retry(monkeypatch) -> None:
    """S6/S7: per-work jobs are enqueued with an RQ Retry so transient failures re-run."""
    wid = "11111111-1111-1111-1111-111111111111"
    captured: dict = {}

    class _RetryCapturingQueue(_FakeQueue):
        def enqueue(self, func, *args, job_id=None, retry=None, **kw):
            captured["retry"] = retry
            return super().enqueue(func, *args, job_id=job_id, **kw)

    monkeypatch.setattr(queue, "get_queue", lambda: _RetryCapturingQueue())
    monkeypatch.setattr(queue, "_live_coalesced_job", lambda _conn, _key: None)
    job_id = queue.enqueue_enrichment(wid)
    assert job_id is not None and job_id.startswith(f"enrich-{wid}-")
    retry = captured["retry"]
    assert retry is not None and retry.max == 2
    assert retry.intervals == [30, 120]
