"""Local agent protocol endpoints.

``/enroll-request`` is intentionally unauthenticated: the agent has no user session and proves
itself with the owner-issued enrollment token. The manifest/teleport endpoints require a valid
agent bearer token (minted on owner approval), and the server only ever handles opaque file
identity + agent-pushed bytes — never a path on the agent's machine.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import require_agent_token
from app.db.session import get_db
from app.models.agent import Agent
from app.schemas.agent import (
    AgentManifestRequest,
    AgentRegisterRequest,
    AgentRegisterResponse,
    PendingTeleportItem,
)
from app.services import agent_files
from app.services import agents as agent_service
from app.workers.queue import enqueue_extraction

router = APIRouter()
DB_DEP = Depends(get_db)
AGENT_DEP = Depends(require_agent_token)

_MAX_UPLOAD_BYTES = 200 * 1024 * 1024  # 200 MB hard limit, mirrors /imports/upload


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


@router.post("/manifest", status_code=status.HTTP_202_ACCEPTED)
def receive_manifest(
    payload: AgentManifestRequest,
    db: Session = DB_DEP,
    agent: Agent = AGENT_DEP,
) -> dict[str, int | str]:
    """Receive a scanned-file manifest from an authenticated agent."""
    received = agent_files.ingest_manifest(db, agent=agent, items=payload.items)
    db.commit()
    return {"status": "accepted", "received": received}


@router.get("/teleports/pending", response_model=list[PendingTeleportItem])
def list_pending_teleports(
    db: Session = DB_DEP, agent: Agent = AGENT_DEP
) -> list[PendingTeleportItem]:
    """List this agent's files a user has requested to teleport (the agent then uploads them)."""
    return [
        PendingTeleportItem(
            local_file_id=item.local_file_id, sha256=item.sha256, display_path=item.display_path
        )
        for item in agent_files.pending_teleports(db, agent=agent)
    ]


@router.post("/teleports/{local_file_id}/content", status_code=status.HTTP_201_CREATED)
async def upload_teleport_content(
    local_file_id: str,
    file: UploadFile,
    db: Session = DB_DEP,
    agent: Agent = AGENT_DEP,
) -> dict[str, str]:
    """Agent pushes the bytes for a requested teleport; the server verifies the hash + stores it."""
    pdf_bytes = await file.read(_MAX_UPLOAD_BYTES + 1)
    if len(pdf_bytes) > _MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Uploaded file exceeds 200 MB limit",
        )
    if len(pdf_bytes) < 4 or pdf_bytes[:4] != b"%PDF":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded content is not a valid PDF"
        )
    try:
        stored = agent_files.complete_teleport(
            db, agent=agent, local_file_id=local_file_id, pdf_bytes=pdf_bytes
        )
    except ValueError as exc:
        db.commit()  # persist the teleport.failed audit/state before surfacing the error
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    db.commit()
    db.refresh(stored)
    enqueue_extraction(stored.id)
    return {"file_id": str(stored.id), "sha256": stored.sha256, "status": "complete"}
