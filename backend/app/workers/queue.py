"""RQ background-job queue helpers.

Redis/RQ are imported lazily so importing this module never requires a Redis
connection (keeps unit tests and the import path light). Enqueueing is best-effort:
if the queue is unavailable, callers (e.g. folder import) must still succeed.
"""

import logging

from app.core.config import get_settings

logger = logging.getLogger(__name__)

QUEUE_NAME = "paracord"
EXTRACT_JOB = "app.workers.jobs.extract_pdf_job"
ENRICH_JOB = "app.workers.jobs.enrich_work_job"
EMBED_JOB = "app.workers.jobs.embed_work_job"


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


def enqueue_embedding(work_id) -> str | None:
    """Best-effort enqueue of an embedding-index job (keeps embeddings off the search read path)."""
    try:
        job = get_queue().enqueue(EMBED_JOB, str(work_id))
        return job.id
    except Exception as exc:  # noqa: BLE001 - best effort; log and continue
        logger.warning("Could not enqueue embedding for work %s: %s", work_id, exc)
        return None


_FUNC_LABELS = {EXTRACT_JOB: "extract", ENRICH_JOB: "enrich", EMBED_JOB: "embed"}


def clear_jobs(which: str = "finished_failed") -> dict:
    """Clear job history from the registries. Returns counts removed (never raises).

    ``which``: ``finished_failed`` (default — the noise), ``failed``, ``finished``, or ``all``
    (also drops still-queued jobs). Running jobs are never touched.
    """
    try:
        from redis import Redis
        from rq import Queue
        from rq.job import Job

        conn = Redis.from_url(get_settings().redis_url)
        conn.ping()
        queue = Queue(QUEUE_NAME, connection=conn)

        targets = {
            "finished_failed": ("finished", "failed"),
            "failed": ("failed",),
            "finished": ("finished",),
            "all": ("finished", "failed", "queued"),
        }.get(which, ("finished", "failed"))

        registries = {
            "finished": queue.finished_job_registry,
            "failed": queue.failed_job_registry,
        }
        removed = 0
        for name in targets:
            if name == "queued":
                removed += queue.count
                queue.empty()
                continue
            registry = registries[name]
            for job_id in list(registry.get_job_ids()):
                try:
                    Job.fetch(job_id, connection=conn).delete()
                    removed += 1
                except Exception:  # noqa: BLE001 - job may already be gone
                    registry.remove(job_id)
        return {"available": True, "cleared": removed}
    except Exception as exc:  # noqa: BLE001 - report unavailability instead of crashing
        return {"available": False, "error": str(exc), "cleared": 0}


def queue_status(limit: int = 25) -> dict:
    """Introspect the RQ queue: counts, workers, and recent jobs.

    Returns ``{"available": False, ...}`` (never raises) when Redis is unreachable, so the UI
    can tell the user the background worker isn't available rather than silently doing nothing.
    """
    empty_counts = {
        "queued": 0,
        "started": 0,
        "finished": 0,
        "failed": 0,
        "scheduled": 0,
        "deferred": 0,
    }
    try:
        from redis import Redis
        from rq import Queue, Worker
        from rq.job import Job

        conn = Redis.from_url(get_settings().redis_url)
        conn.ping()

        queue = Queue(QUEUE_NAME, connection=conn)
        counts = {
            "queued": queue.count,
            "started": queue.started_job_registry.count,
            "finished": queue.finished_job_registry.count,
            "failed": queue.failed_job_registry.count,
            "scheduled": queue.scheduled_job_registry.count,
            "deferred": queue.deferred_job_registry.count,
        }

        def _label(job) -> str:
            return _FUNC_LABELS.get(job.func_name, (job.func_name or "job").rsplit(".", 1)[-1])

        def _collect(job_ids, fallback_status: str) -> list[dict]:
            rows: list[dict] = []
            for job_id in list(job_ids)[:limit]:
                try:
                    job = Job.fetch(job_id, connection=conn)
                except Exception:  # noqa: BLE001 - job may expire between listing and fetch
                    continue
                rows.append(
                    {
                        "id": job.id,
                        "task": _label(job),
                        "status": (job.get_status(refresh=False) or fallback_status),
                        "enqueued_at": job.enqueued_at.isoformat() if job.enqueued_at else None,
                        "ended_at": job.ended_at.isoformat() if job.ended_at else None,
                        "error": (job.exc_info or "").strip()[-2000:] or None
                        if fallback_status == "failed"
                        else None,
                    }
                )
            return rows

        # Newest-relevant first: failures, then running, queued, recent finished.
        jobs = (
            _collect(queue.failed_job_registry.get_job_ids(), "failed")
            + _collect(queue.started_job_registry.get_job_ids(), "started")
            + _collect(queue.job_ids, "queued")
            + _collect(list(reversed(queue.finished_job_registry.get_job_ids())), "finished")
        )
        return {
            "available": True,
            "workers": Worker.count(connection=conn),
            "counts": counts,
            "jobs": jobs[:limit],
        }
    except Exception as exc:  # noqa: BLE001 - report unavailability instead of crashing
        return {
            "available": False,
            "error": str(exc),
            "workers": 0,
            "counts": empty_counts,
            "jobs": [],
        }
