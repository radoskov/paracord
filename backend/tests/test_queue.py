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
