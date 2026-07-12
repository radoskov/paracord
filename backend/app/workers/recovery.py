"""Self-healing recovery sweep for owed extractions (D7).

An import commits the File/Work rows and *then* enqueues extraction best-effort. If Redis was down
at enqueue time the job is silently dropped, so the import "succeeds" but the file never gets
extracted. To recover, every import sets a durable ``File.extraction_requested_at`` marker in the
same commit; this sweep finds files that are still owed an extraction and re-enqueues them.

Safe to run from multiple API workers concurrently and while Redis is down:
* the deterministic extraction job id (``extract-{file_id}``) plus the live-job guard in
  :func:`app.workers.queue.enqueue_extraction` make a re-enqueue idempotent (D7 invariant 2), and
* a dead Redis makes ``enqueue_extraction`` return ``None`` without raising, so the sweep simply
  skips those files and tries again next startup.

Only files with the marker set are ever considered — a file nobody asked to extract (marker NULL,
e.g. attach-without-extract) is never swept (D7 invariant 1).
"""

import logging

from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.models.file import MAX_EXTRACTION_ATTEMPTS, File
from app.workers.queue import enqueue_extraction

logger = logging.getLogger(__name__)


def owed_extraction_file_ids(db) -> list:
    """Return ids of files still owed an extraction (marker set, attempts below the cap).

    An in-flight file is NOT double-enqueued here: the marker is still set while the worker runs,
    but ``enqueue_extraction``'s deterministic-job-id live-guard makes re-enqueuing a
    queued/started/scheduled job a no-op — and, unlike a durable "extracting" status, that guard
    self-heals if the worker dies (a dead job is no longer live, so the file is recovered on the
    next sweep). Files that already hit the retry cap (F2) are terminal and excluded.
    """
    stmt = (
        select(File.id)
        .where(File.extraction_requested_at.is_not(None))
        .where(File.extraction_attempts < MAX_EXTRACTION_ATTEMPTS)
    )
    return list(db.scalars(stmt).all())


def _redis_reachable() -> bool:
    """Best-effort Redis liveness check so a down queue skips the whole sweep cheaply."""
    try:
        from redis import Redis

        Redis.from_url(get_settings().redis_url).ping()
        return True
    except Exception as exc:  # noqa: BLE001 - report unreachable instead of raising
        logger.info("Recovery sweep skipped: Redis unreachable (%s)", exc)
        return False


def sweep_owed_extractions() -> dict:
    """Re-enqueue extraction for every file still owed one. Never raises.

    Returns ``{"considered": int, "requeued": int, "skipped": int, "redis_reachable": bool}``.
    Idempotent via the deterministic job id: a file already queued/running is left alone.
    """
    if not _redis_reachable():
        return {"considered": 0, "requeued": 0, "skipped": 0, "redis_reachable": False}

    requeued = 0
    considered = 0
    try:
        with SessionLocal() as db:
            file_ids = owed_extraction_file_ids(db)
        considered = len(file_ids)
        for file_id in file_ids:
            if enqueue_extraction(file_id) is not None:
                requeued += 1
    except Exception as exc:  # noqa: BLE001 - a recovery sweep must never crash startup
        logger.warning("Recovery sweep failed: %s", exc)
    if considered:
        logger.info("Recovery sweep re-enqueued %d/%d owed extraction(s)", requeued, considered)
    return {
        "considered": considered,
        "requeued": requeued,
        "skipped": considered - requeued,
        "redis_reachable": True,
    }


def _extracted_work_ids_subquery():
    """Subquery of work ids that have at least one linked file whose extraction has completed."""
    from app.models.file import File, FileWorkLink

    return (
        select(FileWorkLink.work_id)
        .join(File, File.id == FileWorkLink.file_id)
        .where(File.status == "extracted")
    )


def owed_chunk_work_ids(db) -> list:
    """Extracted works with zero passage chunks — chunking never ran (F2 derive-from-state).

    A worker crash / dropped enqueue between extraction and chunking leaves a paper extracted but
    un-chunked (so it's missing from chunk-level semantic search); this recovers it. Merged shadows
    are excluded. Idempotent: chunking is deterministic, so a re-run is a no-op once chunks exist.
    """
    from app.models.chunk import WorkChunk
    from app.models.work import Work

    stmt = select(Work.id).where(
        Work.merged_into_id.is_(None),
        Work.id.in_(_extracted_work_ids_subquery()),
        Work.id.notin_(select(WorkChunk.work_id)),
    )
    return list(db.scalars(stmt).all())


def owed_embedding_work_ids(db) -> list:
    """Extracted works with no embedding at all — embedding never ran (F2 derive-from-state).

    ``index_one_work`` always writes a document-level embedding (even for a title-only paper), so an
    extracted work with zero ``Embedding`` rows means the embed stage was dropped. Recover it so the
    paper is searchable. Merged shadows excluded. (A model *switch* is the separate reindex flow.)
    """
    from app.models.ai import Embedding
    from app.models.work import Work

    has_embedding = select(Embedding.entity_id).where(Embedding.entity_type == "work")
    stmt = select(Work.id).where(
        Work.merged_into_id.is_(None),
        Work.id.in_(_extracted_work_ids_subquery()),
        Work.id.notin_(has_embedding),
    )
    return list(db.scalars(stmt).all())


def sweep_owed_downstream() -> dict:
    """Enqueue chunk/embed for extracted works that never got them (F2). Never raises.

    The extract→enrich→chunk→embed chain is linked by fire-and-forget enqueues; a worker crash or a
    Redis flap between stages can leave a paper extracted but never chunked/embedded, with no owed
    marker. This derives the "owed" set from state and re-enqueues, idempotently (deterministic job
    ids coalesce). Skipped entirely when Redis is unreachable.
    """
    if not _redis_reachable():
        return {"chunk": 0, "embed": 0, "redis_reachable": False}

    from app.workers.queue import enqueue_chunking, enqueue_embedding

    chunk_n = embed_n = 0
    try:
        with SessionLocal() as db:
            chunk_ids = owed_chunk_work_ids(db)
            embed_ids = owed_embedding_work_ids(db)
        chunk_n = sum(1 for wid in chunk_ids if enqueue_chunking(str(wid)) is not None)
        embed_n = sum(1 for wid in embed_ids if enqueue_embedding(str(wid)) is not None)
    except Exception as exc:  # noqa: BLE001 - a recovery sweep must never crash startup
        logger.warning("Downstream recovery sweep failed: %s", exc)
    if chunk_n or embed_n:
        logger.info("Downstream recovery sweep: chunk=%d embed=%d", chunk_n, embed_n)
    return {"chunk": chunk_n, "embed": embed_n, "redis_reachable": True}
