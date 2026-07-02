"""Background-job (RQ queue) status endpoint.

Read-only visibility into the extraction/enrichment queue so the UI can show whether a task is
queued, running, finished, or failed — and whether the background worker is available at all.
"""

from fastapi import APIRouter, Depends, Query

from app.api.deps import require_admin, require_min_role
from app.core.security import Role
from app.models.user import User
from app.workers.queue import clear_jobs, queue_status

router = APIRouter()
EDITOR_DEP = Depends(require_min_role(Role.EDITOR))
ADMIN_DEP = Depends(require_admin)


@router.get("")
def get_job_status(limit: int = Query(default=25, ge=1, le=100)) -> dict:
    """Return queue counts, worker count, and recent jobs (or availability=False)."""
    return queue_status(limit)


@router.post("/clear")
def clear_job_history(
    which: str = Query(
        default="finished_failed", pattern="^(finished_failed|failed|finished|all)$"
    ),
    _: User = EDITOR_DEP,
) -> dict:
    """Clear finished/failed (and optionally queued) job history. Running jobs are untouched."""
    return clear_jobs(which)


@router.post("/reprocess-pending")
def reprocess_pending(_: User = ADMIN_DEP) -> dict:
    """Re-enqueue extraction for every file still owed one (the D7 recovery sweep, on demand).

    Idempotent: a file already queued/running is left alone. Degrades gracefully (never 500) when
    the queue is offline — reports ``redis_reachable: false`` instead.
    """
    from app.workers.recovery import sweep_owed_extractions

    return sweep_owed_extractions()
