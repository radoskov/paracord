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
