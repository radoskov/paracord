"""Background-job (RQ queue) status endpoint.

Read-only visibility into the extraction/enrichment queue so the UI can show whether a task is
queued, running, finished, or failed — and whether the background worker is available at all.
"""

from fastapi import APIRouter, Query

from app.workers.queue import queue_status

router = APIRouter()


@router.get("")
def get_job_status(limit: int = Query(default=25, ge=1, le=100)) -> dict:
    """Return queue counts, worker count, and recent jobs (or availability=False)."""
    return queue_status(limit)
