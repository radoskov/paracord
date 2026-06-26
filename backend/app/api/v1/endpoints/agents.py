"""Local agent protocol endpoints.

``/enroll-request`` is intentionally unauthenticated: the agent has no user session and proves
itself with the owner-issued enrollment token. The remaining manifest/teleport endpoints require
a valid agent bearer token (minted on owner approval) and return 501 until M5 is implemented.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import require_agent_token
from app.db.session import get_db
from app.schemas.agent import AgentManifestRequest, AgentRegisterRequest, AgentRegisterResponse
from app.services import agents as agent_service

router = APIRouter()
DB_DEP = Depends(get_db)
AGENT_AUTH = Depends(require_agent_token)


class AgentEnrollRequest(BaseModel):
    token: str
    name: str


class AgentEnrollResponse(BaseModel):
    agent_id: uuid.UUID
    status: str


@router.post(
    "/enroll-request", response_model=AgentEnrollResponse, status_code=status.HTTP_202_ACCEPTED
)
def enroll_request(payload: AgentEnrollRequest, db: Session = DB_DEP) -> AgentEnrollResponse:
    """Request enrollment with an owner-issued token; creates a pending agent (202)."""
    try:
        agent = agent_service.enroll_agent(db, token=payload.token, name=payload.name)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    db.commit()
    return AgentEnrollResponse(agent_id=agent.id, status=agent.status)


@router.post("/register", response_model=AgentRegisterResponse)
def register_agent(payload: AgentRegisterRequest) -> AgentRegisterResponse:
    """Deprecated registration stub; use ``/enroll-request`` + owner approval instead."""
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail="Deprecated; use /agents/enroll-request and owner approval.",
    )


@router.post("/manifest", dependencies=[AGENT_AUTH])
def receive_manifest(payload: AgentManifestRequest) -> dict[str, str]:
    """Receive a scanned-file manifest from an authenticated agent (M5, not yet implemented)."""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Manifest ingestion is not implemented yet.",
    )


@router.post("/teleport/{agent_file_id}", dependencies=[AGENT_AUTH])
def request_teleport(agent_file_id: str) -> dict[str, str]:
    """Request a teleport of an agent-owned file to managed storage (M5, not yet implemented)."""
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="Teleport is not implemented yet.",
    )
