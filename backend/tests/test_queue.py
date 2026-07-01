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
