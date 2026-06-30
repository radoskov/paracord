"""Owner-only admin endpoints: user management and audit-log access (SPEC 10.2)."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import require_owner
from app.db.session import get_db
from app.models.agent import Agent, AgentFile
from app.models.audit import AuditEvent
from app.models.user import User
from app.schemas.user import UserCreate, UserOut, UserRoleUpdate
from app.services import agents as agent_service
from app.services import users as user_service
from app.services.audit import record_event

router = APIRouter()


class EnrollTokenOut(BaseModel):
    token: str
    expires_at: str


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
    _owner: User = Depends(require_owner),
) -> list[User]:
    """List all user accounts (owner only)."""
    return user_service.list_users(db)


@router.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: UserCreate,
    db: Session = Depends(get_db),
    owner: User = Depends(require_owner),
) -> User:
    """Create a user account (owner only)."""
    try:
        user = user_service.create_user(
            db,
            username=payload.username,
            password=payload.password,
            role=payload.role,
            actor_user_id=owner.id,
        )
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
    owner: User = Depends(require_owner),
) -> User:
    """Change a user's role (owner only)."""
    try:
        user = user_service.set_user_role(
            db, user_id=user_id, role=payload.role, actor_user_id=owner.id
        )
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
    owner: User = Depends(require_owner),
) -> User:
    """Disable a user account (owner only)."""
    try:
        user = user_service.disable_user(db, user_id=user_id, actor_user_id=owner.id)
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
    owner: User = Depends(require_owner),
) -> User:
    """Re-enable a disabled user account (owner only)."""
    try:
        user = user_service.enable_user(db, user_id=user_id, actor_user_id=owner.id)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    db.commit()
    db.refresh(user)
    return user


@router.get("/audit-events")
def list_audit_events(
    db: Session = Depends(get_db),
    _owner: User = Depends(require_owner),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict:
    """Return a paginated, newest-first view of audit events (owner only)."""
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


@router.post(
    "/agents/enroll-token", response_model=EnrollTokenOut, status_code=status.HTTP_201_CREATED
)
def issue_agent_enroll_token(
    db: Session = Depends(get_db),
    owner: User = Depends(require_owner),
) -> EnrollTokenOut:
    """Mint a single-use agent enrollment token (owner only)."""
    raw_token, token = agent_service.mint_enrollment_token(db, owner=owner)
    db.commit()
    return EnrollTokenOut(token=raw_token, expires_at=token.expires_at.isoformat())


@router.get("/agents", response_model=list[AgentOut])
def list_agents(
    db: Session = Depends(get_db),
    _owner: User = Depends(require_owner),
) -> list:
    """List enrolled/pending agents (owner only)."""
    return agent_service.list_agents(db)


@router.get("/agents/{agent_id}/files", response_model=list[AgentFileOut])
def list_agent_files(
    agent_id: uuid.UUID,
    db: Session = Depends(get_db),
    _owner: User = Depends(require_owner),
) -> list:
    """List the files an agent has reported via its manifest (owner only)."""
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
    owner: User = Depends(require_owner),
) -> Agent:
    """Set an agent's privileges (owner only)."""
    agent = db.get(Agent, agent_id)
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")
    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(agent, field, value)
    record_event(
        db,
        "agent.privileges_changed",
        actor_user_id=owner.id,
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
    owner: User = Depends(require_owner),
) -> Agent:
    """Rename an agent (owner only)."""
    try:
        agent = agent_service.rename_agent(db, agent_id=agent_id, name=payload.name, owner=owner)
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
    owner: User = Depends(require_owner),
) -> None:
    """Remove an agent and its indexed-file records, revoking its token (owner only)."""
    try:
        agent_service.delete_agent(db, agent_id=agent_id, owner=owner)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    db.commit()


@router.post("/agents/{agent_id}/approve", response_model=AgentApprovedOut)
def approve_agent(
    agent_id: uuid.UUID,
    db: Session = Depends(get_db),
    owner: User = Depends(require_owner),
) -> AgentApprovedOut:
    """Approve a pending agent and return its scoped access token once (owner only)."""
    try:
        raw_token, agent = agent_service.approve_agent(db, agent_id=agent_id, owner=owner)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    db.commit()
    return AgentApprovedOut(agent_id=agent.id, status=agent.status, agent_token=raw_token)
