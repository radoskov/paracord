"""RQ background-job queue helpers.

Redis/RQ are imported lazily so importing this module never requires a Redis
connection (keeps unit tests and the import path light). Enqueueing is best-effort:
if the queue is unavailable, callers (e.g. folder import) must still succeed.
"""

import contextlib
import logging

from app.core.config import get_settings

logger = logging.getLogger(__name__)

QUEUE_NAME = "paracord"
EXTRACT_JOB = "app.workers.jobs.extract_pdf_job"
STAGE_EXTRACT_JOB = "app.workers.jobs.extract_staging_item_job"
ENRICH_JOB = "app.workers.jobs.enrich_work_job"
EMBED_JOB = "app.workers.jobs.embed_work_job"
CHUNK_JOB = "app.workers.jobs.chunk_work_job"
DEDUP_JOB = "app.workers.jobs.scan_duplicates_job"
REINDEX_JOB = "app.workers.jobs.reindex_embeddings_job"
PULL_MODEL_JOB = "app.workers.jobs.pull_model_job"
TOPIC_JOB = "app.workers.jobs.topic_work_job"
KEYWORDS_JOB = "app.workers.jobs.keywords_work_job"
SUMMARY_SCOPE_JOB = "app.workers.jobs.summarize_scope_job"
TOPIC_SCOPE_JOB = "app.workers.jobs.topic_model_job"
BM25_REBUILD_JOB = "app.workers.jobs.rebuild_bm25_job"
REF_MATCH_JOB = "app.workers.jobs.rescan_reference_matches_job"
REF_CONSOLIDATE_JOB = "app.workers.jobs.consolidate_references_job"
BACKUP_EXPORT_JOB = "app.workers.jobs.export_backup_job"
BACKUP_RESTORE_JOB = "app.workers.jobs.restore_backup_job"

# Deterministic id so a burst of edits coalesces into a single pending rebuild (D13a).
BM25_REBUILD_JOB_ID = "bm25-rebuild"
# Deterministic id so repeated "rescan all references" clicks coalesce into one pending job.
REF_MATCH_JOB_ID = "reference-rescan-all"
# Deterministic id so the startup hook and the admin button coalesce into one consolidation run.
REF_CONSOLIDATE_JOB_ID = "reference-consolidation"


def get_queue():
    """Return an RQ queue bound to the configured Redis URL."""
    from redis import Redis
    from rq import Queue

    return Queue(
        QUEUE_NAME, connection=Redis.from_url(get_settings().redis_url), default_timeout=900
    )


def pending_queue_depth() -> int | None:
    """Return the number of pending (queued) jobs, or None when Redis is unreachable (D39).

    Fail-open by design: a None result means the depth couldn't be measured, and the capacity
    guard must then allow the request (a dropped enqueue is already surfaced via D7's
    ``extraction_queued=false``). Never raises.
    """
    try:
        from redis import Redis
        from rq import Queue

        conn = Redis.from_url(get_settings().redis_url)
        conn.ping()
        return Queue(QUEUE_NAME, connection=conn).count
    except Exception as exc:  # noqa: BLE001 - fail open when the depth can't be measured
        logger.warning("Could not measure pending queue depth: %s", exc)
        return None


# Job statuses that mean an extraction for a file is already in flight — a re-enqueue while the
# file is in one of these is a no-op (the deterministic job id collides), so a file is never
# enqueued twice (D7 invariant 2: no collision between the recovery sweep and a user re-extract).
_LIVE_JOB_STATUSES = {"queued", "started", "deferred", "scheduled"}


def extraction_job_id(file_id) -> str:
    """Deterministic RQ job id for a file's extraction (``extract-{file_id}``).

    A stable id makes re-enqueuing an already-queued/running file a no-op instead of a duplicate.
    A dash (not a colon) separator is required: RQ 2.x rejects ``:`` in job ids, and a UUID file id
    only contains hex digits and dashes.
    """
    return f"extract-{file_id}"


def _live_job_id(conn, job_id: str) -> str | None:
    """Return ``job_id`` if a job with that id is still in flight, else None.

    Only a genuinely-missing job (``NoSuchJobError``) yields None-without-raising; a dead Redis
    connection propagates so the caller reports the enqueue as failed. A terminal (finished/failed)
    job also yields None, so a legitimate re-run re-enqueues with the same deterministic id.
    """
    from rq.exceptions import NoSuchJobError
    from rq.job import Job

    try:
        job = Job.fetch(job_id, connection=conn)
    except NoSuchJobError:
        return None
    return job.id if job.get_status(refresh=True) in _LIVE_JOB_STATUSES else None


def enqueue_extraction(file_id, *, force_ocr: bool = False) -> str | None:
    """Best-effort enqueue of a GROBID extraction job. Returns the job id, or None.

    Uses the deterministic id ``extract-{file_id}`` and skips the enqueue when a live job with that
    id already exists, so the same file is queued exactly once even if the recovery sweep and a
    user's re-extract race (D7 invariant 2). ``force_ocr`` re-runs OCRmyPDF regardless of the
    configured backend / current text-layer quality (#22). Never raises: a missing/unreachable
    Redis must not break the import flow (callers surface the ``None`` as ``extraction_queued``).
    """
    job_id = extraction_job_id(file_id)
    try:
        from rq import Retry

        queue = get_queue()
        existing = _live_job_id(queue.connection, job_id)
        if existing is not None:
            return existing
        # F2: retry a *transient* extraction failure automatically (fast, in-Redis). The job itself
        # zeroes retries_left on a terminal/cap failure so those aren't re-run; the durable
        # File.extraction_attempts cap bounds retries across restarts (the recovery sweep backstop).
        job = queue.enqueue(
            EXTRACT_JOB,
            str(file_id),
            force_ocr,
            job_id=job_id,
            retry=Retry(max=2, interval=[15, 60]),
        )
        return job.id
    except Exception as exc:  # noqa: BLE001 - best effort; log and continue
        logger.warning("Could not enqueue extraction for file %s: %s", file_id, exc)
        return None


def enqueue_staging_extraction(item_id) -> str | None:
    """Best-effort enqueue of a record-free extraction for a staged multi-import PDF (batch10 #1).

    Deterministic id ``stage-extract-{item_id}`` so a re-enqueue of an in-flight item is a no-op.
    Never raises: callers fall back to inline extraction when this returns None (queue unavailable).
    """
    job_id = f"stage-extract-{item_id}"
    try:
        queue = get_queue()
        existing = _live_job_id(queue.connection, job_id)
        if existing is not None:
            return existing
        return queue.enqueue(STAGE_EXTRACT_JOB, str(item_id), job_id=job_id).id
    except Exception as exc:  # noqa: BLE001 - best effort; caller extracts inline on None
        logger.warning("Could not enqueue staging extraction for item %s: %s", item_id, exc)
        return None


def _enqueue_work_job(job_func: str, prefix: str, work_id) -> str | None:
    """Best-effort enqueue of a per-work job under a deterministic id ``{prefix}-{work_id}``.

    Like extraction (D7), a stable id makes a re-enqueue of an already in-flight job for the same
    work a no-op instead of a duplicate — so a manual re-run racing the auto-chain (extract →
    enrich → chunk → embed) can't spawn two concurrent jobs whose interleaved writes make results
    vary run-to-run (issue 1c). A terminal prior job (finished/failed) does not block a fresh run.
    Never raises: a missing/unreachable Redis must not break the caller.
    """
    job_id = f"{prefix}-{work_id}"
    try:
        from rq import Retry

        queue = get_queue()
        existing = _live_job_id(queue.connection, job_id)
        if existing is not None:
            return existing
        # S6/S7: transient failures (rate-limited external APIs, network blips, a briefly-down
        # Postgres) re-run automatically. Deterministic failures don't reach the retry: the jobs
        # catch them (e.g. enrich's DOI-conflict path) and record processing_error WITHOUT raising,
        # so only genuinely unexpected/transient exceptions trigger a re-run.
        return queue.enqueue(
            job_func, str(work_id), job_id=job_id, retry=Retry(max=2, interval=[30, 120])
        ).id
    except Exception as exc:  # noqa: BLE001 - best effort; log and continue
        logger.warning("Could not enqueue %s for work %s: %s", prefix, work_id, exc)
        return None


def enqueue_enrichment(work_id) -> str | None:
    """Best-effort enqueue of an external metadata-enrichment job. Returns job id, or None."""
    return _enqueue_work_job(ENRICH_JOB, "enrich", work_id)


def enqueue_embedding(work_id) -> str | None:
    """Best-effort enqueue of an embedding-index job (keeps embeddings off the search read path)."""
    return _enqueue_work_job(EMBED_JOB, "embed", work_id)


def enqueue_chunking(work_id) -> str | None:
    """Best-effort enqueue of a passage-chunking job (populates work_chunks for semantic search)."""
    return _enqueue_work_job(CHUNK_JOB, "chunk", work_id)


def enqueue_topics(work_id) -> str | None:
    """Best-effort enqueue of a per-paper topic-modeling job. Returns the job id, or None."""
    return _enqueue_work_job(TOPIC_JOB, "topic", work_id)


def enqueue_keywords(work_id) -> str | None:
    """Best-effort enqueue of a per-paper keyword-extraction job. Returns the job id, or None."""
    return _enqueue_work_job(KEYWORDS_JOB, "keywords", work_id)


def enqueue_duplicate_scan() -> str | None:
    """Best-effort enqueue of a full-library duplicate scan (kept off the request path)."""
    try:
        job = get_queue().enqueue(DEDUP_JOB)
        return job.id
    except Exception as exc:  # noqa: BLE001 - best effort; log and continue
        logger.warning("Could not enqueue duplicate scan: %s", exc)
        return None


def enqueue_reference_consolidation() -> str | None:
    """Best-effort enqueue of the canonical-reference consolidation scan (S13/S14). Id or None.

    Fixed id ``reference-consolidation``: the startup hook and repeated admin-button clicks
    coalesce into one pending run."""
    try:
        queue = get_queue()
        existing = _live_job_id(queue.connection, REF_CONSOLIDATE_JOB_ID)
        if existing is not None:
            return existing
        return queue.enqueue(REF_CONSOLIDATE_JOB, job_id=REF_CONSOLIDATE_JOB_ID).id
    except Exception as exc:  # noqa: BLE001 - best effort; log and continue
        logger.warning("Could not enqueue reference consolidation: %s", exc)
        return None


def enqueue_backup_export(*, include_pdfs: bool, actor_user_id: str) -> str | None:
    """Best-effort enqueue of a backup export (fixed id: repeated clicks coalesce)."""
    job_id = "backup-export"
    try:
        queue = get_queue()
        existing = _live_job_id(queue.connection, job_id)
        if existing is not None:
            return existing
        return queue.enqueue(
            BACKUP_EXPORT_JOB,
            args=(include_pdfs, actor_user_id),
            job_id=job_id,
            job_timeout=3600,
        ).id
    except Exception as exc:  # noqa: BLE001 - best effort; the endpoint falls back to inline
        logger.warning("Could not enqueue backup export: %s", exc)
        return None


def enqueue_backup_restore(
    *, archive: str, mode: str, pdf_root_alias: str | None, actor_user_id: str
) -> str | None:
    """Best-effort enqueue of a backup restore (fixed id: one restore at a time)."""
    job_id = "backup-restore"
    try:
        queue = get_queue()
        existing = _live_job_id(queue.connection, job_id)
        if existing is not None:
            return existing
        return queue.enqueue(
            BACKUP_RESTORE_JOB,
            args=(archive, mode, pdf_root_alias, actor_user_id),
            job_id=job_id,
            job_timeout=7200,
        ).id
    except Exception as exc:  # noqa: BLE001 - best effort; the endpoint falls back to inline
        logger.warning("Could not enqueue backup restore: %s", exc)
        return None


def enqueue_reference_rescan() -> str | None:
    """Best-effort enqueue of a full-library reference→work rematch (batch 12). Returns id or None.

    Uses the fixed id ``reference-rescan-all`` and skips the enqueue when a live job with that id
    already exists, so repeated clicks coalesce into a single pending rescan."""
    try:
        queue = get_queue()
        existing = _live_job_id(queue.connection, REF_MATCH_JOB_ID)
        if existing is not None:
            return existing
        return queue.enqueue(REF_MATCH_JOB, job_id=REF_MATCH_JOB_ID).id
    except Exception as exc:  # noqa: BLE001 - best effort; log and continue
        logger.warning("Could not enqueue reference rescan: %s", exc)
        return None


def enqueue_bm25_rebuild() -> str | None:
    """Best-effort enqueue of a background BM25F+ lexical-index rebuild (D13a). Returns id, or None.

    Uses the fixed id ``bm25-rebuild`` and skips the enqueue when a live job with that id already
    exists, so a burst of edits coalesces into a single pending rebuild instead of stacking many.
    Never raises: with the queue unavailable the search simply keeps serving the stale index.
    """
    try:
        queue = get_queue()
        existing = _live_job_id(queue.connection, BM25_REBUILD_JOB_ID)
        if existing is not None:
            return existing
        return queue.enqueue(BM25_REBUILD_JOB, job_id=BM25_REBUILD_JOB_ID).id
    except Exception as exc:  # noqa: BLE001 - best effort; log and continue
        logger.warning("Could not enqueue BM25F+ rebuild: %s", exc)
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


def _enqueue_scope_job(
    job_func: str, prefix: str, scope_type: str, scope_id, **kwargs
) -> str | None:
    """Best-effort enqueue of a per-scope AI job (S15) under a deterministic id.

    ``{prefix}-{scope_type}-{scope_id|library}``: re-clicking while a run for the same scope is in
    flight returns the live job instead of stacking a duplicate. Never raises (queue-down -> None,
    the caller falls back to running inline).
    """
    job_id = f"{prefix}-{scope_type}-{scope_id or 'library'}"
    try:
        queue = get_queue()
        existing = _live_job_id(queue.connection, job_id)
        if existing is not None:
            return existing
        return queue.enqueue(
            job_func,
            args=(scope_type, str(scope_id) if scope_id else None),
            kwargs=kwargs,
            job_id=job_id,
        ).id
    except Exception as exc:  # noqa: BLE001 - best effort; log and continue
        logger.warning(
            "Could not enqueue %s for scope %s/%s: %s", prefix, scope_type, scope_id, exc
        )
        return None


def enqueue_scope_summary(scope_type: str, scope_id, **kwargs) -> str | None:
    """Best-effort enqueue of a large-scope summary job (S15). Returns the job id, or None."""
    return _enqueue_scope_job(SUMMARY_SCOPE_JOB, "summary-scope", scope_type, scope_id, **kwargs)


def enqueue_scope_topics(scope_type: str, scope_id, **kwargs) -> str | None:
    """Best-effort enqueue of a large-scope topic-model job (S15). Returns the job id, or None."""
    return _enqueue_scope_job(TOPIC_SCOPE_JOB, "topics-scope", scope_type, scope_id, **kwargs)


_FUNC_LABELS = {
    EXTRACT_JOB: "extract",
    ENRICH_JOB: "enrich",
    EMBED_JOB: "embed",
    CHUNK_JOB: "chunk",
    TOPIC_JOB: "topic",
    KEYWORDS_JOB: "keywords",
    SUMMARY_SCOPE_JOB: "summary-scope",
    REF_CONSOLIDATE_JOB: "reference-consolidation",
    BACKUP_EXPORT_JOB: "backup-export",
    BACKUP_RESTORE_JOB: "backup-restore",
    TOPIC_SCOPE_JOB: "topics-scope",
    DEDUP_JOB: "dedup-scan",
    REINDEX_JOB: "reindex",
    PULL_MODEL_JOB: "model-pull",
    BM25_REBUILD_JOB: "bm25-rebuild",
}


def cancel_job(job_id: str) -> bool:
    """Cancel a queued/scheduled/deferred job (S-batch item 3). Returns whether it was cancelled.

    Only jobs that have not started run: a started job keeps running (stopping mid-execution risks
    half-applied work). ``job.cancel()`` removes the job from whichever registry holds it; a
    scheduled retry is additionally dropped from the scheduled registry.
    """
    try:
        from rq.exceptions import NoSuchJobError
        from rq.job import Job

        queue = get_queue()
        try:
            job = Job.fetch(job_id, connection=queue.connection)
        except NoSuchJobError:
            return False
        if job.get_status(refresh=True) not in {"queued", "scheduled", "deferred"}:
            return False
        job.cancel()
        with contextlib.suppress(Exception):
            queue.scheduled_job_registry.remove(job_id)
        return True
    except Exception as exc:  # noqa: BLE001 - best effort; a dead Redis just reports "not cancelled"
        logger.warning("Could not cancel job %s: %s", job_id, exc)
        return False


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


def empty_queue() -> dict:
    """Empty the pending RQ queue (queued jobs only). Returns how many were dropped (D39).

    Running, finished and failed jobs are left untouched — only jobs still waiting to start are
    removed. Never raises: reports ``available: False`` when Redis is unreachable so the admin
    endpoint degrades gracefully instead of 500-ing.
    """
    try:
        from redis import Redis
        from rq import Queue

        conn = Redis.from_url(get_settings().redis_url)
        conn.ping()
        queue = Queue(QUEUE_NAME, connection=conn)
        dropped = queue.count
        queue.empty()
        return {"available": True, "dropped": dropped}
    except Exception as exc:  # noqa: BLE001 - report unavailability instead of crashing
        return {"available": False, "error": str(exc), "dropped": 0}


# The API can only reset queue *state* in Redis; the worker processes themselves run under the
# supervisor in the worker container, so a full process reset is a container restart.
WORKER_PROCESS_RESET_HINT = "A full worker process reset is `docker compose restart worker`."


def recover_stuck_jobs() -> dict:
    """Recover jobs stuck in RQ's StartedJobRegistry and clear the FailedJobRegistry (D39).

    A job stranded as "started" (its worker died mid-job) is requeued so it runs again; one that
    can't be requeued is dropped from the registry. The failed-job history is then cleared. This
    recovers "something got stuck" without restarting the worker *processes* — those live in the
    worker container under the supervisor, so a full process reset is a ``docker compose restart
    worker`` (returned as ``note``). Never raises: reports ``available: False`` when Redis is down.
    """
    try:
        from redis import Redis
        from rq import Queue
        from rq.job import Job

        conn = Redis.from_url(get_settings().redis_url)
        conn.ping()
        queue = Queue(QUEUE_NAME, connection=conn)

        started = queue.started_job_registry
        requeued = 0
        for job_id in list(started.get_job_ids()):
            try:
                started.requeue(job_id)
                requeued += 1
            except Exception:  # noqa: BLE001 - can't requeue (job gone/expired) → drop it
                with contextlib.suppress(Exception):
                    started.remove(job_id, delete_job=True)

        failed = queue.failed_job_registry
        cleared_failed = 0
        for job_id in list(failed.get_job_ids()):
            try:
                Job.fetch(job_id, connection=conn).delete()
            except Exception:  # noqa: BLE001 - job may already be gone
                failed.remove(job_id)
            cleared_failed += 1

        return {
            "available": True,
            "requeued": requeued,
            "cleared_failed": cleared_failed,
            "note": WORKER_PROCESS_RESET_HINT,
        }
    except Exception as exc:  # noqa: BLE001 - report unavailability instead of crashing
        return {
            "available": False,
            "error": str(exc),
            "requeued": 0,
            "cleared_failed": 0,
            "note": WORKER_PROCESS_RESET_HINT,
        }


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
        staging_ids = {
            uid
            for j in jobs
            if j.get("target_kind") == "staging_item" and (uid := _as_uuid(j.get("target_id")))
        }
        work_ids = {
            uid
            for j in jobs
            if j.get("target_kind") == "work" and (uid := _as_uuid(j.get("target_id")))
        }
        if not file_ids and not work_ids and not staging_ids:
            return

        # title := Work.canonical_title; hash := File.sha256 (Work has no hash). Resolve titles/
        # hashes for both file- and work-targeted jobs, batched, in one short-lived session.
        files: dict = {}  # File.id -> (sha256, canonical_title-via-link)
        works: dict = {}  # Work.id -> (canonical_title, sha256-of-primary-file)
        stagings: dict = {}  # ImportStagingItem.id -> (parsed-title-or-filename, sha256)
        with SessionLocal() as db:
            if staging_ids:
                from app.models.import_staging import ImportStagingItem

                for row in db.scalars(
                    select(ImportStagingItem).where(ImportStagingItem.id.in_(staging_ids))
                ):
                    title = (row.parsed or {}).get("title") or row.filename
                    stagings[row.id] = (title, row.sha256)
            file_links: dict = {}  # File.id -> first linked Work.id
            work_links: dict = {}  # Work.id -> first linked File.id
            if file_ids:
                for link in db.scalars(
                    select(FileWorkLink).where(FileWorkLink.file_id.in_(file_ids))
                ):
                    file_links.setdefault(link.file_id, link.work_id)
            if work_ids:
                for link in db.scalars(
                    select(FileWorkLink).where(FileWorkLink.work_id.in_(work_ids))
                ):
                    work_links.setdefault(link.work_id, link.file_id)
            need_files = file_ids | set(work_links.values())
            need_works = work_ids | set(file_links.values())
            shas = (
                dict(db.execute(select(File.id, File.sha256).where(File.id.in_(need_files))).all())
                if need_files
                else {}
            )
            titles = (
                dict(
                    db.execute(
                        select(Work.id, Work.canonical_title).where(Work.id.in_(need_works))
                    ).all()
                )
                if need_works
                else {}
            )
            # File-target jobs: sha256 from File; title from the file's linked Work (if any).
            for fid in file_ids:
                if fid in shas:
                    files[fid] = (shas[fid], titles.get(file_links.get(fid)))
            # Work-target jobs: title from Work; sha256 from the work's first/primary linked File.
            for wid in work_ids:
                if wid in titles:
                    works[wid] = (titles[wid], shas.get(work_links.get(wid)))

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
            elif kind == "staging_item":
                sid = _as_uuid(j.get("target_id"))
                if sid in stagings:
                    title, sha = stagings[sid]
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
            if job.func_name == STAGE_EXTRACT_JOB:
                return "staging_item", str(args[0])
            if job.func_name in (ENRICH_JOB, EMBED_JOB, CHUNK_JOB, TOPIC_JOB, KEYWORDS_JOB):
                return "work", str(args[0])
            return None, None

        def _job_error(job) -> str | None:
            """Failed-job traceback tail via job.latest_result() (job.exc_info is deprecated)."""
            try:
                result = job.latest_result()
            except Exception:  # noqa: BLE001 - result payload may be gone; error text is best-effort
                return None
            exc_string = getattr(result, "exc_string", None) if result is not None else None
            return (exc_string or "").strip()[-2000:] or None

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
                        "error": _job_error(job) if fallback_status == "failed" else None,
                        "target_kind": kind,
                        "target_id": target_id,
                        "paper_title": None,
                        "paper_sha256": None,
                        # F2: retries remaining on this job's RQ Retry budget (None = no retry
                        # policy). A "scheduled" job with retries_left set is a pending retry — the
                        # Jobs tab labels it so users see the app is retrying, not stuck.
                        "retries_left": getattr(job, "retries_left", None),
                    }
                )
            return rows

        jobs = (
            _collect(queue.failed_job_registry.get_job_ids(), "failed")
            + _collect(queue.started_job_registry.get_job_ids(), "started")
            + _collect(queue.scheduled_job_registry.get_job_ids(), "scheduled")
            + _collect(queue.job_ids, "queued")
            + _collect(queue.finished_job_registry.get_job_ids(), "finished")
        )
        jobs = _order_jobs_newest_first(jobs)[:limit]
        _resolve_paper_targets(jobs)
        # Worker count is scoped to THIS queue (a worker attached elsewhere can't drain our jobs) so
        # the Jobs-tab semaphore can distinguish "reachable but nothing consuming" from healthy.
        worker_count = Worker.count(queue=queue)
        return {
            "available": True,
            # D7 queue-health fields (semaphore): explicit, self-describing names alongside the
            # legacy ``available``/``workers`` keys the UI already reads.
            "redis_reachable": True,
            # E1: whether the deployment requires Redis (fail-closed). The UI shows a red
            # "limits unavailable" banner when this is true while ``redis_reachable`` is false.
            "require_redis": get_settings().production_require_redis,
            "worker_count": worker_count,
            "queued": counts["queued"],
            "workers": worker_count,
            "counts": counts,
            "jobs": jobs,
        }
    except Exception as exc:  # noqa: BLE001 - report unavailability instead of crashing
        return {
            "available": False,
            "error": str(exc),
            "redis_reachable": False,
            "require_redis": get_settings().production_require_redis,
            "worker_count": 0,
            "queued": 0,
            "workers": 0,
            "counts": empty_counts,
            "jobs": [],
        }
