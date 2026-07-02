"""Queue-length capacity guard (D39).

A single entry point, :func:`assert_queue_has_capacity`, that job-creating requests call before
enqueuing background work. It measures the current pending RQ queue depth and rejects the request
with HTTP 429 when the queue is already at the configured ``max_queue_len`` ceiling. Fail-open: if
Redis is unreachable the depth can't be measured, so the request is allowed (a dropped enqueue is
already surfaced by D7's ``extraction_queued=false``).
"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.services.app_config import effective_max_queue_len
from app.workers import queue


def assert_queue_has_capacity(db: Session) -> None:
    """Reject with 429 when the pending queue is at capacity; allow (no-op) if it can't be measured.

    Called at the start of every job-creating request. When Redis is unreachable
    :func:`queue.pending_queue_depth` returns ``None`` and this guard is a no-op (fail-open).
    """
    depth = queue.pending_queue_depth()
    if depth is None:
        return
    limit = effective_max_queue_len(db)
    if depth >= limit:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"The processing queue is full ({depth} pending). Please wait and retry.",
        )
