"""Queue-length capacity guard (D39).

A single entry point, :func:`assert_queue_has_capacity`, that job-creating requests call before
enqueuing background work. It measures the current pending RQ queue depth and rejects the request
with HTTP 429 when the queue is already at the configured ``max_queue_len`` ceiling. Fail-open: if
Redis is unreachable the depth can't be measured, so the request is allowed (a dropped enqueue is
already surfaced by D7's ``extraction_queued=false``). A LAN/production deployment can flip this to
fail closed with ``PARACORD_PRODUCTION_REQUIRE_REDIS`` (E1): an unmeasurable queue then yields 503.
"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.services.app_config import effective_max_queue_len
from app.workers import queue


def assert_queue_has_capacity(db: Session) -> None:
    """Reject with 429 when the pending queue is at capacity; allow (no-op) if it can't be measured.

    Called at the start of every job-creating request. When Redis is unreachable
    :func:`queue.pending_queue_depth` returns ``None``; this guard is then a no-op (fail-open) unless
    ``PARACORD_PRODUCTION_REQUIRE_REDIS`` is set, in which case it rejects with 503 (E1 fail-closed).
    """
    depth = queue.pending_queue_depth()
    if depth is None:
        if get_settings().production_require_redis:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "The processing queue is unavailable (Redis unreachable) and the server "
                    "requires it; retry shortly."
                ),
            )
        return
    limit = effective_max_queue_len(db)
    if depth >= limit:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"The processing queue is full ({depth} pending). Please wait and retry.",
        )
