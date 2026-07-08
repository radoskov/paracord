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


def test_enqueue_work_job_uses_deterministic_id_and_skips_when_in_flight(monkeypatch) -> None:
    """1c: a per-work enqueue uses a stable ``{prefix}-{work_id}`` id and is a no-op while a job
    with that id is already in flight (so a manual re-run can't race the auto-chain)."""
    wid = "11111111-1111-1111-1111-111111111111"

    fake = _FakeQueue()
    monkeypatch.setattr(queue, "get_queue", lambda: fake)
    # No live job → enqueues under the deterministic id.
    monkeypatch.setattr(queue, "_live_job_id", lambda _conn, _jid: None)
    assert queue.enqueue_enrichment(wid) == f"enrich-{wid}"
    assert fake.enqueued == [(queue.ENRICH_JOB, (wid,), f"enrich-{wid}")]

    # A live job with the same id → skip the enqueue, return the existing id.
    fake2 = _FakeQueue()
    monkeypatch.setattr(queue, "get_queue", lambda: fake2)
    monkeypatch.setattr(queue, "_live_job_id", lambda _conn, jid: jid)
    assert queue.enqueue_keywords(wid) == f"keywords-{wid}"
    assert fake2.enqueued == []  # not enqueued a second time


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
