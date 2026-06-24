"""RQ background-job queue helpers.

Redis/RQ are imported lazily so importing this module never requires a Redis
connection (keeps unit tests and the import path light). Enqueueing is best-effort:
if the queue is unavailable, callers (e.g. folder import) must still succeed.
"""

import logging

from app.core.config import get_settings

logger = logging.getLogger(__name__)

QUEUE_NAME = "paperracks"
EXTRACT_JOB = "app.workers.jobs.extract_pdf_job"
ENRICH_JOB = "app.workers.jobs.enrich_work_job"


def get_queue():
    """Return an RQ queue bound to the configured Redis URL."""
    from redis import Redis
    from rq import Queue

    return Queue(QUEUE_NAME, connection=Redis.from_url(get_settings().redis_url))


def enqueue_extraction(file_id) -> str | None:
    """Best-effort enqueue of a GROBID extraction job. Returns the job id, or None.

    Never raises: a missing/unreachable Redis must not break the import flow.
    """
    try:
        job = get_queue().enqueue(EXTRACT_JOB, str(file_id))
        return job.id
    except Exception as exc:  # noqa: BLE001 - best effort; log and continue
        logger.warning("Could not enqueue extraction for file %s: %s", file_id, exc)
        return None


def enqueue_enrichment(work_id) -> str | None:
    """Best-effort enqueue of an external metadata-enrichment job. Returns job id, or None."""
    try:
        job = get_queue().enqueue(ENRICH_JOB, str(work_id))
        return job.id
    except Exception as exc:  # noqa: BLE001 - best effort; log and continue
        logger.warning("Could not enqueue enrichment for work %s: %s", work_id, exc)
        return None
