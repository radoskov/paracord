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
CHUNK_JOB = "app.workers.jobs.chunk_work_job"
DEDUP_JOB = "app.workers.jobs.scan_duplicates_job"
REINDEX_JOB = "app.workers.jobs.reindex_embeddings_job"
PULL_MODEL_JOB = "app.workers.jobs.pull_model_job"
TOPIC_JOB = "app.workers.jobs.topic_work_job"
KEYWORDS_JOB = "app.workers.jobs.keywords_work_job"


def get_queue():
    """Return an RQ queue bound to the configured Redis URL."""
    from redis import Redis
    from rq import Queue

    return Queue(QUEUE_NAME, connection=Redis.from_url(get_settings().redis_url))


def enqueue_extraction(file_id, *, force_ocr: bool = False) -> str | None:
    """Best-effort enqueue of a GROBID extraction job. Returns the job id, or None.

    ``force_ocr`` re-runs OCRmyPDF regardless of the configured backend / current text-layer
    quality (#22). Never raises: a missing/unreachable Redis must not break the import flow.
    """
    try:
        job = get_queue().enqueue(EXTRACT_JOB, str(file_id), force_ocr)
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


def enqueue_chunking(work_id) -> str | None:
    """Best-effort enqueue of a passage-chunking job (populates work_chunks for semantic search)."""
    try:
        job = get_queue().enqueue(CHUNK_JOB, str(work_id))
        return job.id
    except Exception as exc:  # noqa: BLE001 - best effort; log and continue
        logger.warning("Could not enqueue chunking for work %s: %s", work_id, exc)
        return None


def enqueue_topics(work_id) -> str | None:
    """Best-effort enqueue of a per-paper topic-modeling job. Returns the job id, or None."""
    try:
        job = get_queue().enqueue(TOPIC_JOB, str(work_id))
        return job.id
    except Exception as exc:  # noqa: BLE001 - best effort; log and continue
        logger.warning("Could not enqueue topics for work %s: %s", work_id, exc)
        return None


def enqueue_keywords(work_id) -> str | None:
    """Best-effort enqueue of a per-paper keyword-extraction job. Returns the job id, or None."""
    try:
        job = get_queue().enqueue(KEYWORDS_JOB, str(work_id))
        return job.id
    except Exception as exc:  # noqa: BLE001 - best effort; log and continue
        logger.warning("Could not enqueue keywords for work %s: %s", work_id, exc)
        return None


def enqueue_duplicate_scan() -> str | None:
    """Best-effort enqueue of a full-library duplicate scan (kept off the request path)."""
    try:
        job = get_queue().enqueue(DEDUP_JOB)
        return job.id
    except Exception as exc:  # noqa: BLE001 - best effort; log and continue
        logger.warning("Could not enqueue duplicate scan: %s", exc)
        return None


def enqueue_reindex() -> str | None:
    """Best-effort enqueue of a full embedding reindex for the active provider."""
    try:
        return get_queue().enqueue(REINDEX_JOB).id
    except Exception as exc:  # noqa: BLE001 - best effort; log and continue
        logger.warning("Could not enqueue reindex: %s", exc)
        return None


def enqueue_model_pull(provider: str, model: str) -> str | None:
    """Best-effort enqueue of a model download/pull (long-running; tracked as a job)."""
    try:
        return get_queue().enqueue(PULL_MODEL_JOB, provider, model, job_timeout=3600).id
    except Exception as exc:  # noqa: BLE001 - best effort; log and continue
        logger.warning("Could not enqueue model pull %s/%s: %s", provider, model, exc)
        return None


_FUNC_LABELS = {
    EXTRACT_JOB: "extract",
    ENRICH_JOB: "enrich",
    EMBED_JOB: "embed",
    CHUNK_JOB: "chunk",
    TOPIC_JOB: "topic",
    KEYWORDS_JOB: "keywords",
    DEDUP_JOB: "dedup-scan",
    REINDEX_JOB: "reindex",
    PULL_MODEL_JOB: "model-pull",
}


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


def _resolve_paper_targets(jobs: list[dict]) -> None:
    """Best-effort: fill ``paper_title``/``paper_sha256`` from the DB for paper-targeted jobs.

    Mutates ``jobs`` in place. Guarded so it NEVER raises — the queue endpoint is best-effort and
    must keep returning even when the DB is unreachable. DB/model imports are local so this module
    stays import-light (no DB connection at import time).
    """
    try:
        import uuid

        from sqlalchemy import select

        from app.db.session import SessionLocal
        from app.models.file import File, FileWorkLink
        from app.models.work import Work

        def _as_uuid(value) -> uuid.UUID | None:
            try:
                return uuid.UUID(str(value))
            except Exception:  # noqa: BLE001 - skip un-parseable ids
                return None

        file_ids = {
            uid
            for j in jobs
            if j.get("target_kind") == "file" and (uid := _as_uuid(j.get("target_id")))
        }
        work_ids = {
            uid
            for j in jobs
            if j.get("target_kind") == "work" and (uid := _as_uuid(j.get("target_id")))
        }
        if not file_ids and not work_ids:
            return

        # title := Work.canonical_title; hash := File.sha256 (Work has no hash). Resolve titles/
        # hashes for both file- and work-targeted jobs, batched, in one short-lived session.
        files: dict = {}  # File.id -> (sha256, canonical_title-via-link)
        works: dict = {}  # Work.id -> (canonical_title, sha256-of-primary-file)
        with SessionLocal() as db:
            # File-target jobs: sha256 from File; title from the file's linked Work (if any).
            for fid in file_ids:
                file = db.get(File, fid)
                if file is None:
                    continue
                title = None
                link = db.scalar(select(FileWorkLink).where(FileWorkLink.file_id == fid))
                if link is not None:
                    work = db.get(Work, link.work_id)
                    title = work.canonical_title if work else None
                files[fid] = (file.sha256, title)
            # Work-target jobs: title from Work; sha256 from the work's first/primary linked File.
            for wid in work_ids:
                work = db.get(Work, wid)
                if work is None:
                    continue
                sha = None
                link = db.scalar(select(FileWorkLink).where(FileWorkLink.work_id == wid))
                if link is not None:
                    file = db.get(File, link.file_id)
                    sha = file.sha256 if file else None
                works[wid] = (work.canonical_title, sha)

        for j in jobs:
            kind = j.get("target_kind")
            if kind == "file":
                fid = _as_uuid(j.get("target_id"))
                if fid in files:
                    sha, title = files[fid]
                    j["paper_sha256"] = sha
                    j["paper_title"] = title
            elif kind == "work":
                wid = _as_uuid(j.get("target_id"))
                if wid in works:
                    title, sha = works[wid]
                    j["paper_title"] = title
                    j["paper_sha256"] = sha
    except Exception as exc:  # noqa: BLE001 - best-effort enrichment; never break the endpoint
        logger.warning("Could not resolve paper targets for jobs: %s", exc)


# Job statuses considered "active" (still in flight): these always sort above terminal jobs.
_ACTIVE_STATUSES = {"started", "queued", "deferred", "scheduled"}


def _order_jobs_newest_first(jobs: list[dict]) -> list[dict]:
    """Order jobs so the newest activity is on top (item 9), mutating and returning ``jobs``.

    Active jobs (running/queued) sort above terminal ones (finished/failed); within each band the
    most recent job is first. Recency is ``ended_at`` when present (terminal jobs) else
    ``enqueued_at`` — ISO-8601 timestamps compare chronologically as strings, and a missing
    timestamp ("") sorts last on the descending pass. Two stable sorts: recency descending first,
    then the active/terminal band, which preserves the recency order inside each band.
    """
    jobs.sort(key=lambda j: j.get("ended_at") or j.get("enqueued_at") or "", reverse=True)
    jobs.sort(key=lambda j: 0 if j.get("status") in _ACTIVE_STATUSES else 1)
    return jobs


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

        def _target(job) -> tuple[str | None, str | None]:
            """(kind, id) of the paper this job acts on — ('file'|'work', uuid-str) or (None, None).

            EXTRACT job's first arg is a File id; ENRICH/EMBED/TOPIC/KEYWORDS a Work id.
            Dedup/reindex/model-pull act on no single paper.
            """
            args = list(getattr(job, "args", None) or [])
            if not args:
                return None, None
            if job.func_name == EXTRACT_JOB:
                return "file", str(args[0])
            if job.func_name in (ENRICH_JOB, EMBED_JOB, CHUNK_JOB, TOPIC_JOB, KEYWORDS_JOB):
                return "work", str(args[0])
            return None, None

        def _collect(job_ids, fallback_status: str) -> list[dict]:
            rows: list[dict] = []
            for job_id in list(job_ids)[:limit]:
                try:
                    job = Job.fetch(job_id, connection=conn)
                except Exception:  # noqa: BLE001 - job may expire between listing and fetch
                    continue
                kind, target_id = _target(job)
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
                        "target_kind": kind,
                        "target_id": target_id,
                        "paper_title": None,
                        "paper_sha256": None,
                    }
                )
            return rows

        jobs = (
            _collect(queue.failed_job_registry.get_job_ids(), "failed")
            + _collect(queue.started_job_registry.get_job_ids(), "started")
            + _collect(queue.job_ids, "queued")
            + _collect(queue.finished_job_registry.get_job_ids(), "finished")
        )
        jobs = _order_jobs_newest_first(jobs)[:limit]
        _resolve_paper_targets(jobs)
        return {
            "available": True,
            "workers": Worker.count(connection=conn),
            "counts": counts,
            "jobs": jobs,
        }
    except Exception as exc:  # noqa: BLE001 - report unavailability instead of crashing
        return {
            "available": False,
            "error": str(exc),
            "workers": 0,
            "counts": empty_counts,
            "jobs": [],
        }
