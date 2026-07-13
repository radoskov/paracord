"""Background-job (RQ queue) status endpoint.

Read-only visibility into the extraction/enrichment queue so the UI can show whether a task is
queued, running, finished, or failed — and whether the background worker is available at all.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import require_admin, require_min_role
from app.core.security import Role
from app.db.session import get_db
from app.models.user import User
from app.services.audit import record_event
from app.workers.queue import clear_jobs, empty_queue, queue_status, recover_stuck_jobs

router = APIRouter()
DB_DEP = Depends(get_db)
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
    """Re-enqueue everything still owed processing (the recovery sweeps, on demand).

    Runs both the D7 owed-extraction sweep and the F2 downstream sweep (chunk/embed for works
    extracted but never indexed). Idempotent: anything already queued/running is left alone.
    Degrades gracefully (never 500) when the queue is offline — reports ``redis_reachable: false``.
    """
    from app.workers.recovery import sweep_owed_downstream, sweep_owed_extractions

    result = sweep_owed_extractions()
    result["downstream"] = sweep_owed_downstream()
    return result


@router.post("/clear-queue")
def clear_queue(db: Session = DB_DEP, actor: User = ADMIN_DEP) -> dict:
    """Empty the pending job queue (admin). Running jobs are untouched; returns how many dropped.

    Degrades gracefully (never 500) when Redis is unreachable — reports ``available: false``.
    """
    result = empty_queue()
    record_event(
        db,
        "queue.cleared",
        actor_user_id=actor.id,
        entity_type="queue",
        details={"dropped": result.get("dropped", 0), "available": result.get("available", False)},
    )
    db.commit()
    return result


@router.post("/reset-workers")
def reset_workers(db: Session = DB_DEP, actor: User = ADMIN_DEP) -> dict:
    """Recover stuck jobs (admin): requeue jobs stranded as started and clear failed history.

    Cannot restart the worker *processes* (they run under the supervisor in the worker container);
    the response ``note`` says a full reset is ``docker compose restart worker``. Degrades
    gracefully (never 500) when Redis is unreachable.
    """
    result = recover_stuck_jobs()
    record_event(
        db,
        "queue.workers_reset",
        actor_user_id=actor.id,
        entity_type="queue",
        details={
            "requeued": result.get("requeued", 0),
            "cleared_failed": result.get("cleared_failed", 0),
            "available": result.get("available", False),
        },
    )
    db.commit()
    return result


@router.post("/{job_id}/cancel")
def cancel_job_endpoint(job_id: str, _: User = EDITOR_DEP) -> dict:
    """Cancel a queued/scheduled/deferred job (a started job keeps running).

    Lets an editor drop a pending retry or a stuck scheduled job from the queue instead of
    waiting it out.
    """
    from app.workers.queue import cancel_job

    cancelled = cancel_job(job_id)
    if not cancelled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Job not found, already finished, or already running",
        )
    return {"cancelled": True, "job_id": job_id}


@router.get("/{job_id}/result")
def get_job_result(job_id: str, actor: User = EDITOR_DEP) -> dict:
    """Status + stored result of a background analysis job (requester-gated; L-a).

    Poll until ``status == "finished"``, then use ``result`` — large-scope graphs are computed on
    the worker and their payloads held in Redis for an hour.
    """
    from app.core.security import Role
    from app.workers.queue import fetch_job_result

    out = fetch_job_result(
        job_id,
        requester_id=str(actor.id),
        is_admin=actor.role in (Role.OWNER, Role.ADMIN),
    )
    if out["status"] == "forbidden":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return out
