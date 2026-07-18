"""AI recommendation endpoints (Insights → Recommend categorization).

POST /recommend finds a fresh cached run for (scope + settings + model) or creates one and enqueues
the background job; GET /recommend/{id} returns the cached run (requester-gated) — the frontend polls
its ``status`` until done. Accept actions reuse the existing shelf/tag endpoints (nothing here writes
memberships)."""

import hashlib
import json
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_contributor
from app.api.scope_params import resolve_scope_or_404
from app.db.session import get_db
from app.models.recommendation import LIBRARY_SCOPE_SENTINEL, RecommendationRun
from app.models.user import User
from app.services import access
from app.services.ai_config import get_ai_config
from app.services.scope_resolution import SCOPE_TYPES, resolve_scope_works
from app.workers.queue import enqueue_recommend

router = APIRouter()
DB_DEP = Depends(get_db)
# Running a recommendation is a read-oriented analysis (contributor+); accepting a suggestion into a
# shelf/tag is separately gated by the shelf/tag endpoints.
AUTH_DEP = Depends(require_contributor)

_MODES = ("tags", "categorization")
_SCORINGS = ("ranking", "affinity")
_COMBINES = ("sum", "median", "max")
DEFAULT_CAP = 100
HARD_MAX_CAP = 500
MAX_K = 50


class RecommendRequest(BaseModel):
    scope_type: str
    scope_id: uuid.UUID | None = None
    work_ids: list[uuid.UUID] | None = None  # for search_result / selected_papers
    mode: str = "categorization"
    k: int = 5
    scoring: str = "ranking"
    parent_combine: str = "sum"
    prefilter: bool = False
    cap: int = DEFAULT_CAP
    recompute: bool = False


class RecommendRunRead(BaseModel):
    id: uuid.UUID
    scope_type: str
    mode: str
    status: str
    params: dict[str, Any] | None = None
    model_name: str | None = None
    provider_used: str | None = None
    fallback: bool = False
    error: str | None = None
    result: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime
    # Populated only on the POST that enqueues (so the UI can show/cancel the job); None otherwise.
    job_id: str | None = None


def _planned_model(db: Session) -> str:
    """The model identifier that will produce the run — part of the cache key (so a model change
    invalidates the cache). Matches ``resolve_rankers``' ``provider_used`` prefix."""
    cfg = get_ai_config(db)
    if cfg.summary_provider == "local_llm" and cfg.summary_model:
        return f"local_llm:{cfg.summary_model}"
    return f"embedding:{cfg.embedding_provider}"


def _params_hash(payload: RecommendRequest, k: int, cap: int, model: str) -> str:
    blob = json.dumps(
        {
            "mode": payload.mode,
            "k": k,
            "scoring": payload.scoring,
            "parent_combine": payload.parent_combine,
            "prefilter": payload.prefilter,
            "cap": cap,
            "model": model,
        },
        sort_keys=True,
    )
    return hashlib.sha256(blob.encode()).hexdigest()[:32]


def _scope_key(
    scope_type: str, scope_id: uuid.UUID | None, work_ids: list[uuid.UUID] | None
) -> uuid.UUID:
    """A concrete cache key id for the scope: the library sentinel, the container id, or a stable
    hash of an explicit work-id set (search_result / selected_papers)."""
    if scope_id is not None:
        return scope_id
    if work_ids:
        digest = hashlib.sha256(",".join(sorted(str(w) for w in work_ids)).encode()).digest()
        return uuid.UUID(bytes=digest[:16])
    return LIBRARY_SCOPE_SENTINEL


def _read(run: RecommendationRun, *, job_id: str | None = None) -> RecommendRunRead:
    return RecommendRunRead(
        id=run.id,
        scope_type=run.scope_type,
        mode=run.mode,
        status=run.status,
        params=run.params,
        model_name=run.model_name,
        provider_used=run.provider_used,
        fallback=run.fallback,
        error=run.error,
        result=run.result,
        created_at=run.created_at,
        updated_at=run.updated_at,
        job_id=job_id,
    )


@router.post("", response_model=RecommendRunRead)
def create_recommendation(
    payload: RecommendRequest, db: Session = DB_DEP, actor: User = AUTH_DEP
) -> RecommendRunRead:
    """Return a fresh cached run for these settings, or create + enqueue a new one."""
    if payload.mode not in _MODES:
        raise HTTPException(400, f"mode must be one of {_MODES}")
    if payload.scoring not in _SCORINGS:
        raise HTTPException(400, f"scoring must be one of {_SCORINGS}")
    if payload.parent_combine not in _COMBINES:
        raise HTTPException(400, f"parent_combine must be one of {_COMBINES}")
    if payload.scope_type not in SCOPE_TYPES:
        raise HTTPException(400, f"scope_type must be one of {SCOPE_TYPES}")
    k = max(1, min(payload.k, MAX_K))
    cap = max(1, min(payload.cap, HARD_MAX_CAP))

    # Resolve the scope → concrete, visibility-clamped work ids (capped).
    explicit = resolve_scope_or_404(
        db,
        actor,
        scope_type=payload.scope_type,
        scope_id=payload.scope_id,
        work_ids=payload.work_ids,
    )
    works = resolve_scope_works(
        db,
        payload.scope_type,
        payload.scope_id,
        visible_ids=access.visible_work_condition(db, actor),
        work_ids=explicit,
    )
    all_ids = [w.id for w in works]
    capped = len(all_ids) > cap
    work_ids = [str(w) for w in all_ids[:cap]]

    model = _planned_model(db)
    phash = _params_hash(payload, k, cap, model)
    skey = _scope_key(payload.scope_type, payload.scope_id, payload.work_ids)

    if not payload.recompute:
        cached = db.scalar(
            select(RecommendationRun)
            .where(
                RecommendationRun.scope_type == payload.scope_type,
                RecommendationRun.scope_id == skey,
                RecommendationRun.mode == payload.mode,
                RecommendationRun.params_hash == phash,
                RecommendationRun.model_name == model,
                # Results are visibility-scoped to the creator, so the cache is per-user.
                RecommendationRun.created_by_user_id == actor.id,
                RecommendationRun.status.in_(("running", "done")),
            )
            .order_by(RecommendationRun.created_at.desc())
        )
        if cached is not None:
            return _read(cached)

    run = RecommendationRun(
        scope_type=payload.scope_type,
        scope_id=skey,
        mode=payload.mode,
        params_hash=phash,
        params={
            "k": k,
            "scoring": payload.scoring,
            "parent_combine": payload.parent_combine,
            "prefilter": payload.prefilter,
            "cap": cap,
            "work_ids": work_ids,
            "total_in_scope": len(all_ids),
            "capped": capped,
        },
        model_name=model,
        status="running",
        created_by_user_id=actor.id,
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    job_id = enqueue_recommend(run.id, actor.id)
    if job_id is None:
        # Queue down: don't leave a run stuck "running" with nothing processing it.
        run.status = "failed"
        run.error = "Background worker unavailable — start the worker and try again."
        db.commit()
        db.refresh(run)
    return _read(run, job_id=job_id)


@router.get("/{run_id}", response_model=RecommendRunRead)
def get_recommendation(
    run_id: uuid.UUID, db: Session = DB_DEP, actor: User = AUTH_DEP
) -> RecommendRunRead:
    """Return a run's status + (when done) its cached result. Requester-gated: only the creator or
    an admin/owner may read it (the result can name shelves/rows a stranger shouldn't see)."""
    run = db.get(RecommendationRun, run_id)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Recommendation not found"
        )
    if run.created_by_user_id != actor.id and not access.is_admin_or_owner(actor):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Recommendation not found"
        )
    return _read(run)
