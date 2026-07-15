"""Administration endpoints: user management, agents and audit-log access (SPEC 10.2).

Authorization model (batch 2 #20): general administration is open to {owner, admin}; the privileged
subset — creating/disabling/deleting/role-changing an admin, and any action targeting the owner — is
owner-only and enforced in the user-management service layer. No account may disable or delete
itself.
"""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import require_admin
from app.core.config import get_settings
from app.db.session import get_db
from app.models.agent import Agent, AgentFile
from app.models.audit import AuditEvent
from app.models.user import User
from app.schemas.user import UserCreate, UserOut, UserRoleUpdate
from app.services import agents as agent_service
from app.services import app_config as app_config_service
from app.services import rate_limit
from app.services import users as user_service
from app.services.audit import record_event
from app.services.users import PermissionError403

router = APIRouter()


class EnrollTokenOut(BaseModel):
    token: str
    expires_at: str


class PasswordResetRequest(BaseModel):
    new_password: str


class AppConfigOut(BaseModel):
    max_papers_per_page: int
    rate_limit_per_client_per_min: int
    rate_limit_global_per_min: int
    max_batch_items: int
    rq_worker_count: int
    max_queue_len: int
    # Citing-papers fetch cap (S20): max external citers fetched+cached per paper.
    citing_papers_fetch_cap: int
    # AI scope-job threshold (S15/S16): scopes above this run topics/summaries/graphs on the worker.
    ai_scope_job_threshold: int
    # Per-surface analysis node caps (L-a): highest-degree nodes are kept, hidden counts reported.
    citation_graph_node_cap: int
    topic_graph_node_cap: int
    viz_node_cap: int
    # Reference→library matching (batch 12): treat a fuzzy "likely local" match as a hard link
    # ("use fuzzy auto-accept"), at/above the editable threshold below.
    use_fuzzy_match_as_confirmed: bool
    # Fuzzy auto-accept threshold (UX batch): effective (clamped) similarity_pct.
    fuzzy_accept_threshold: float
    # Yaml-only floor for the threshold above — surfaced so the UI can show/enforce it.
    fuzzy_accept_threshold_min: float
    # High-confidence auto-accept (UX batch): accept a ≥high_confidence_threshold match even
    # without a DOI/arXiv id. The threshold itself is yaml-only (surfaced read-only).
    use_high_confidence_auto_accept: bool
    high_confidence_threshold: float
    # Reference→library matching (F3a): re-run a full library-wide reference rematch on startup.
    reference_rescan_on_startup: bool


class AppConfigUpdate(BaseModel):
    # All fields optional: the admin panel PATCHes only the section it edits (partial update).
    max_papers_per_page: int | None = Field(default=None, ge=1)
    rate_limit_per_client_per_min: int | None = Field(default=None, ge=1)
    rate_limit_global_per_min: int | None = Field(default=None, ge=1)
    max_batch_items: int | None = Field(default=None, ge=1)
    rq_worker_count: int | None = Field(default=None, ge=1)
    max_queue_len: int | None = Field(default=None, ge=1)
    citing_papers_fetch_cap: int | None = Field(default=None, ge=1)
    ai_scope_job_threshold: int | None = Field(default=None, ge=1)
    citation_graph_node_cap: int | None = Field(default=None, ge=1)
    topic_graph_node_cap: int | None = Field(default=None, ge=1)
    viz_node_cap: int | None = Field(default=None, ge=1)
    use_fuzzy_match_as_confirmed: bool | None = Field(default=None)
    # Validated against the yaml floor in the service (400 below it); ge=0 is just a sanity bound.
    fuzzy_accept_threshold: float | None = Field(default=None, ge=0, le=100)
    use_high_confidence_auto_accept: bool | None = Field(default=None)
    reference_rescan_on_startup: bool | None = Field(default=None)


class AgentOut(BaseModel):
    id: uuid.UUID
    name: str
    status: str
    host_alias: str | None = None
    last_seen_at: datetime | None = None
    can_index: bool = True
    can_extract: bool = True
    can_teleport: bool = False
    can_be_requested: bool = True
    processing_visibility: bool = True
    server_status_visibility: bool = True

    model_config = {"from_attributes": True}


class AgentPrivilegesUpdate(BaseModel):
    can_index: bool | None = None
    can_extract: bool | None = None
    can_teleport: bool | None = None
    can_be_requested: bool | None = None
    processing_visibility: bool | None = None
    server_status_visibility: bool | None = None


class AgentRenameRequest(BaseModel):
    name: str


class AgentApprovedOut(BaseModel):
    agent_id: uuid.UUID
    status: str
    agent_token: str


class AgentFileOut(BaseModel):
    id: uuid.UUID
    local_file_id: str
    sha256: str
    size_bytes: int
    display_path: str | None = None
    teleport_status: str
    file_id: uuid.UUID | None = None

    model_config = {"from_attributes": True}


@router.get("/users", response_model=list[UserOut])
def list_users(
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
) -> list[User]:
    """List all user accounts (owner or admin)."""
    return user_service.list_users(db)


@router.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: UserCreate,
    db: Session = Depends(get_db),
    actor: User = Depends(require_admin),
) -> User:
    """Create a user account. Admins may create editors/readers; only the owner creates admins."""
    try:
        user = user_service.create_user(
            db,
            username=payload.username,
            password=payload.password,
            role=payload.role,
            actor=actor,
        )
    except PermissionError403 as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    db.commit()
    db.refresh(user)
    return user


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user_role(
    user_id: uuid.UUID,
    payload: UserRoleUpdate,
    db: Session = Depends(get_db),
    actor: User = Depends(require_admin),
) -> User:
    """Change a user's role. The owner cannot be changed; admin changes are owner-only."""
    try:
        user = user_service.set_user_role(db, user_id=user_id, role=payload.role, actor=actor)
    except PermissionError403 as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    db.commit()
    db.refresh(user)
    return user


@router.post("/users/{user_id}/disable", response_model=UserOut)
def disable_user(
    user_id: uuid.UUID,
    db: Session = Depends(get_db),
    actor: User = Depends(require_admin),
) -> User:
    """Disable a user account. No self-disable; owner is never disablable; admins are owner-only."""
    try:
        user = user_service.disable_user(db, user_id=user_id, actor=actor)
    except PermissionError403 as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    db.commit()
    db.refresh(user)
    return user


@router.post("/users/{user_id}/enable", response_model=UserOut)
def enable_user(
    user_id: uuid.UUID,
    db: Session = Depends(get_db),
    actor: User = Depends(require_admin),
) -> User:
    """Re-enable a disabled user account. Re-enabling an admin is owner-only."""
    try:
        user = user_service.enable_user(db, user_id=user_id, actor=actor)
    except PermissionError403 as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    db.commit()
    db.refresh(user)
    return user


@router.post("/users/{user_id}/reset-password")
def reset_user_password(
    user_id: uuid.UUID,
    payload: PasswordResetRequest,
    db: Session = Depends(get_db),
    actor: User = Depends(require_admin),
) -> dict[str, str | int]:
    """Set a new password for another user and sign out their sessions.

    The owner's password is never resettable here; resetting an admin's is owner-only.
    """
    try:
        revoked = user_service.reset_user_password(
            db, user_id=user_id, new_password=payload.new_password, actor=actor
        )
    except PermissionError403 as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    db.commit()
    return {"status": "ok", "sessions_revoked": revoked}


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(
    user_id: uuid.UUID,
    db: Session = Depends(get_db),
    actor: User = Depends(require_admin),
) -> None:
    """Permanently delete a disabled user (must be disabled first).

    No self-delete; the owner is never deletable; deleting an admin is owner-only.
    """
    try:
        user_service.delete_user(db, user_id=user_id, actor=actor)
    except PermissionError403 as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    db.commit()


@router.get("/audit-events")
def list_audit_events(
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict:
    """Return a paginated, newest-first view of audit events (owner or admin)."""
    total = db.scalar(select(func.count()).select_from(AuditEvent)) or 0
    events = db.scalars(
        select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(limit).offset(offset)
    ).all()
    items = [
        {
            "id": str(event.id),
            "event_type": event.event_type,
            "actor_user_id": str(event.actor_user_id) if event.actor_user_id else None,
            "entity_type": event.entity_type,
            "entity_id": event.entity_id,
            "ip_address": event.ip_address,
            "created_at": event.created_at.isoformat() if event.created_at else None,
            "details": event.details,
        }
        for event in events
    ]
    return {"items": items, "total": total, "limit": limit, "offset": offset}


def _app_config_out(db: Session) -> AppConfigOut:
    return AppConfigOut(
        max_papers_per_page=app_config_service.effective_max_papers_per_page(db),
        rate_limit_per_client_per_min=app_config_service.effective_rate_limit_per_client_per_min(
            db
        ),
        rate_limit_global_per_min=app_config_service.effective_rate_limit_global_per_min(db),
        max_batch_items=app_config_service.effective_max_batch_items(db),
        rq_worker_count=app_config_service.effective_rq_worker_count(db),
        max_queue_len=app_config_service.effective_max_queue_len(db),
        citing_papers_fetch_cap=app_config_service.effective_citing_papers_fetch_cap(db),
        ai_scope_job_threshold=app_config_service.effective_ai_scope_job_threshold(db),
        citation_graph_node_cap=app_config_service.effective_citation_graph_node_cap(db),
        topic_graph_node_cap=app_config_service.effective_topic_graph_node_cap(db),
        viz_node_cap=app_config_service.effective_viz_node_cap(db),
        use_fuzzy_match_as_confirmed=app_config_service.effective_use_fuzzy_match_as_confirmed(db),
        fuzzy_accept_threshold=app_config_service.effective_fuzzy_accept_threshold(db),
        fuzzy_accept_threshold_min=float(
            get_settings().reference_matching_min_auto_accept_threshold
        ),
        use_high_confidence_auto_accept=(
            app_config_service.effective_use_high_confidence_auto_accept(db)
        ),
        high_confidence_threshold=float(
            get_settings().reference_matching_high_confidence_threshold
        ),
        reference_rescan_on_startup=app_config_service.effective_reference_rescan_on_startup(db),
    )


@router.get("/app-config", response_model=AppConfigOut)
def read_app_config(
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
) -> AppConfigOut:
    """Return the runtime app configuration (owner or admin)."""
    return _app_config_out(db)


@router.patch("/app-config", response_model=AppConfigOut)
def update_app_config(
    payload: AppConfigUpdate,
    db: Session = Depends(get_db),
    actor: User = Depends(require_admin),
) -> AppConfigOut:
    """Update the runtime app configuration (owner or admin); only supplied fields change."""
    changed: dict[str, int | bool] = {}
    try:
        if payload.max_papers_per_page is not None:
            changed["max_papers_per_page"] = app_config_service.update_max_papers_per_page(
                db, value=payload.max_papers_per_page, actor_user_id=actor.id
            )
        if (
            payload.rate_limit_per_client_per_min is not None
            or payload.rate_limit_global_per_min is not None
        ):
            app_config_service.update_rate_limits(
                db,
                per_client_per_min=payload.rate_limit_per_client_per_min,
                global_per_min=payload.rate_limit_global_per_min,
                actor_user_id=actor.id,
            )
            if payload.rate_limit_per_client_per_min is not None:
                changed["rate_limit_per_client_per_min"] = payload.rate_limit_per_client_per_min
            if payload.rate_limit_global_per_min is not None:
                changed["rate_limit_global_per_min"] = payload.rate_limit_global_per_min
        if payload.max_batch_items is not None:
            changed["max_batch_items"] = app_config_service.update_max_batch_items(
                db, value=payload.max_batch_items, actor_user_id=actor.id
            )
        if payload.rq_worker_count is not None:
            changed["rq_worker_count"] = app_config_service.update_rq_worker_count(
                db, value=payload.rq_worker_count, actor_user_id=actor.id
            )
        if payload.max_queue_len is not None:
            changed["max_queue_len"] = app_config_service.update_max_queue_len(
                db, value=payload.max_queue_len, actor_user_id=actor.id
            )
        if payload.citing_papers_fetch_cap is not None:
            changed["citing_papers_fetch_cap"] = app_config_service.update_citing_papers_fetch_cap(
                db, value=payload.citing_papers_fetch_cap, actor_user_id=actor.id
            )
        if payload.ai_scope_job_threshold is not None:
            changed["ai_scope_job_threshold"] = app_config_service.update_ai_scope_job_threshold(
                db, value=payload.ai_scope_job_threshold, actor_user_id=actor.id
            )
        if payload.citation_graph_node_cap is not None:
            changed["citation_graph_node_cap"] = app_config_service.update_citation_graph_node_cap(
                db, value=payload.citation_graph_node_cap, actor_user_id=actor.id
            )
        if payload.topic_graph_node_cap is not None:
            changed["topic_graph_node_cap"] = app_config_service.update_topic_graph_node_cap(
                db, value=payload.topic_graph_node_cap, actor_user_id=actor.id
            )
        if payload.viz_node_cap is not None:
            changed["viz_node_cap"] = app_config_service.update_viz_node_cap(
                db, value=payload.viz_node_cap, actor_user_id=actor.id
            )
        if payload.use_fuzzy_match_as_confirmed is not None:
            changed["use_fuzzy_match_as_confirmed"] = (
                app_config_service.update_use_fuzzy_match_as_confirmed(
                    db, value=payload.use_fuzzy_match_as_confirmed, actor_user_id=actor.id
                )
            )
        if payload.fuzzy_accept_threshold is not None:
            changed["fuzzy_accept_threshold"] = app_config_service.update_fuzzy_accept_threshold(
                db, value=payload.fuzzy_accept_threshold, actor_user_id=actor.id
            )
        if payload.use_high_confidence_auto_accept is not None:
            changed["use_high_confidence_auto_accept"] = (
                app_config_service.update_use_high_confidence_auto_accept(
                    db, value=payload.use_high_confidence_auto_accept, actor_user_id=actor.id
                )
            )
        if payload.reference_rescan_on_startup is not None:
            changed["reference_rescan_on_startup"] = (
                app_config_service.update_reference_rescan_on_startup(
                    db, value=payload.reference_rescan_on_startup, actor_user_id=actor.id
                )
            )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    record_event(
        db,
        "app.config_changed",
        actor_user_id=actor.id,
        entity_type="app_config",
        details=changed,
    )
    db.commit()
    # Newly-persisted rate limits take effect after the short config cache expires.
    rate_limit.reset_cache()
    # A more permissive acceptance (either auto-accept toggled ON, or a changed threshold) can
    # promote existing soft likely_matches to hard links — kick off a background library-wide
    # rematch so that happens without waiting for the next extraction (batch 12 / UX batch).
    if (
        payload.use_fuzzy_match_as_confirmed
        or payload.use_high_confidence_auto_accept
        or payload.fuzzy_accept_threshold is not None
    ):
        from app.workers.queue import enqueue_reference_rescan

        enqueue_reference_rescan()
    return _app_config_out(db)


@router.post(
    "/agents/enroll-token", response_model=EnrollTokenOut, status_code=status.HTTP_201_CREATED
)
def issue_agent_enroll_token(
    db: Session = Depends(get_db),
    actor: User = Depends(require_admin),
) -> EnrollTokenOut:
    """Mint a single-use agent enrollment token (owner or admin)."""
    raw_token, token = agent_service.mint_enrollment_token(db, owner=actor)
    db.commit()
    return EnrollTokenOut(token=raw_token, expires_at=token.expires_at.isoformat())


@router.get("/agents", response_model=list[AgentOut])
def list_agents(
    db: Session = Depends(get_db),
    _actor: User = Depends(require_admin),
) -> list:
    """List enrolled/pending agents (owner or admin)."""
    return agent_service.list_agents(db)


@router.get("/agents/{agent_id}/files", response_model=list[AgentFileOut])
def list_agent_files(
    agent_id: uuid.UUID,
    db: Session = Depends(get_db),
    _actor: User = Depends(require_admin),
) -> list:
    """List the files an agent has reported via its manifest (owner or admin)."""
    if db.get(Agent, agent_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    return list(
        db.scalars(
            select(AgentFile)
            .where(AgentFile.agent_id == agent_id)
            .order_by(AgentFile.created_at.desc())
        ).all()
    )


@router.patch("/agents/{agent_id}/privileges", response_model=AgentOut)
def update_agent_privileges(
    agent_id: uuid.UUID,
    payload: AgentPrivilegesUpdate,
    db: Session = Depends(get_db),
    actor: User = Depends(require_admin),
) -> Agent:
    """Set an agent's privileges (owner or admin)."""
    agent = db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(agent, field, value)
    record_event(
        db,
        "agent.privileges_changed",
        actor_user_id=actor.id,
        entity_type="agent",
        entity_id=str(agent.id),
        details=changes,
    )
    db.commit()
    db.refresh(agent)
    return agent


@router.patch("/agents/{agent_id}", response_model=AgentOut)
def rename_agent(
    agent_id: uuid.UUID,
    payload: AgentRenameRequest,
    db: Session = Depends(get_db),
    actor: User = Depends(require_admin),
) -> Agent:
    """Rename an agent (owner or admin)."""
    try:
        agent = agent_service.rename_agent(db, agent_id=agent_id, name=payload.name, owner=actor)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    db.commit()
    db.refresh(agent)
    return agent


@router.delete("/agents/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_agent(
    agent_id: uuid.UUID,
    db: Session = Depends(get_db),
    actor: User = Depends(require_admin),
) -> None:
    """Remove an agent and its indexed-file records, revoking its token (owner or admin)."""
    try:
        agent_service.delete_agent(db, agent_id=agent_id, owner=actor)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    db.commit()


class AgentApproveIn(BaseModel):
    # D3: optional token lifetime in days — omit for a permanent token (the owner's own agents);
    # set it to hand a short-lived token to a temporary user.
    token_ttl_days: int | None = Field(default=None, ge=1, le=3650)


@router.post("/agents/{agent_id}/approve", response_model=AgentApprovedOut)
def approve_agent(
    agent_id: uuid.UUID,
    payload: AgentApproveIn | None = None,
    db: Session = Depends(get_db),
    actor: User = Depends(require_admin),
) -> AgentApprovedOut:
    """Approve a pending agent and return its scoped access token once (owner or admin).

    An optional ``token_ttl_days`` makes the token expire (D3); the default stays permanent.
    """
    try:
        raw_token, agent = agent_service.approve_agent(
            db,
            agent_id=agent_id,
            owner=actor,
            token_ttl_days=payload.token_ttl_days if payload else None,
        )
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    db.commit()
    return AgentApprovedOut(agent_id=agent.id, status=agent.status, agent_token=raw_token)


# --- Reference dupes (S13/S14): consolidation scan + contradiction review -------------------------


class ReferenceDupeEntry(BaseModel):
    id: uuid.UUID
    title: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    year: int | None = None
    resolution_status: str
    resolved_work_id: uuid.UUID | None = None
    resolved_work_title: str | None = None
    suggested_work_id: uuid.UUID | None = None
    suggested_work_title: str | None = None
    citing_count: int = 0
    parked: bool = False


class ReferenceDupeGroup(BaseModel):
    dedup_key: str
    references: list[ReferenceDupeEntry]


class LastConsolidationScan(BaseModel):
    at: datetime | None = None
    groups_scanned: int = 0
    folded: int = 0
    conflicts: int = 0


class ReferenceDupesOut(BaseModel):
    last_scan: LastConsolidationScan | None = None
    conflicts: list[ReferenceDupeGroup]


class ReferenceDupesScanOut(BaseModel):
    queued: bool
    job_id: str | None = None
    # Filled when the queue was unavailable and the scan ran inline instead.
    result: LastConsolidationScan | None = None


class ReferenceDupeResolveIn(BaseModel):
    # The reference whose resolution the admin declares correct for its conflict group.
    winner_reference_id: uuid.UUID


def _last_consolidation_scan(db: Session) -> LastConsolidationScan | None:
    event = db.scalars(
        select(AuditEvent)
        .where(AuditEvent.event_type == "reference.consolidation_completed")
        .order_by(AuditEvent.created_at.desc())
    ).first()
    if event is None:
        return None
    details = event.details or {}
    return LastConsolidationScan(
        at=event.created_at,
        groups_scanned=details.get("groups_scanned", 0),
        folded=details.get("folded", 0),
        conflicts=details.get("conflicts", 0),
    )


@router.get("/reference-dupes", response_model=ReferenceDupesOut)
def read_reference_dupes(
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
) -> ReferenceDupesOut:
    """The pending reference-contradiction groups + the last consolidation-scan summary."""
    from app.services.reference_consolidation import list_conflicts

    return ReferenceDupesOut(
        last_scan=_last_consolidation_scan(db),
        conflicts=[ReferenceDupeGroup(**group) for group in list_conflicts(db)],
    )


@router.post("/reference-dupes/scan", response_model=ReferenceDupesScanOut)
def scan_reference_dupes(
    db: Session = Depends(get_db),
    actor: User = Depends(require_admin),
) -> ReferenceDupesScanOut:
    """Run the canonical-reference consolidation scan (S13/S14).

    Enqueued on the worker under a deterministic id (repeated clicks coalesce); when the queue is
    unavailable the scan runs inline instead — it must stay possible on a Redis-less deployment.
    """
    from app.workers.queue import enqueue_reference_consolidation

    job_id = enqueue_reference_consolidation()
    if job_id is not None:
        record_event(
            db,
            "reference.consolidation_requested",
            actor_user_id=actor.id,
            entity_type="job",
            entity_id=job_id,
        )
        db.commit()
        return ReferenceDupesScanOut(queued=True, job_id=job_id)

    from app.services.reference_consolidation import consolidate_references

    result = consolidate_references(db, actor_user_id=actor.id)
    record_event(
        db,
        "reference.consolidation_completed",
        actor_user_id=actor.id,
        details={
            "groups_scanned": result.groups_scanned,
            "folded": result.folded,
            "conflicts": result.conflicts,
        },
    )
    db.commit()
    return ReferenceDupesScanOut(
        queued=False,
        result=LastConsolidationScan(
            groups_scanned=result.groups_scanned,
            folded=result.folded,
            conflicts=result.conflicts,
        ),
    )


@router.post("/reference-dupes/resolve", response_model=ReferenceDupesOut)
def resolve_reference_dupe(
    payload: ReferenceDupeResolveIn,
    db: Session = Depends(get_db),
    actor: User = Depends(require_admin),
) -> ReferenceDupesOut:
    """Fold one contradiction group using the chosen reference's resolution; returns the fresh list."""
    from app.services.reference_consolidation import list_conflicts, resolve_conflict

    resolve_conflict(db, winner_reference_id=payload.winner_reference_id, actor_user_id=actor.id)
    db.commit()
    return ReferenceDupesOut(
        last_scan=_last_consolidation_scan(db),
        conflicts=[ReferenceDupeGroup(**group) for group in list_conflicts(db)],
    )
