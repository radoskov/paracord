"""Local agent protocol endpoints.

``/enroll-request`` is intentionally unauthenticated: the agent has no user session and proves
itself with the owner-issued enrollment token. The manifest/teleport endpoints require a valid
agent bearer token (minted on owner approval), and the server only ever handles opaque file
identity + agent-pushed bytes — never a path on the agent's machine.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import require_agent_token
from app.db.session import get_db
from app.models.agent import Agent, AgentFile
from app.schemas.agent import (
    AgentFileStatus,
    AgentManifestRequest,
    AgentRegisterRequest,
    AgentRegisterResponse,
    PendingTeleportItem,
    RejectTeleportRequest,
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
    if not agent.can_index:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Agent lacks the index privilege"
        )
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
    if not agent.can_teleport:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Agent lacks the teleport privilege"
        )
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


@router.post("/files/{local_file_id}/extract", status_code=status.HTTP_201_CREATED)
async def upload_for_extraction(
    local_file_id: str,
    file: UploadFile,
    db: Session = DB_DEP,
    agent: Agent = AGENT_DEP,
) -> dict[str, str]:
    """`index_and_extract`: agent uploads a PDF; the server extracts it, keeps a preview, and
    discards the PDF afterwards (only the reference + metadata remain)."""
    if not agent.can_extract:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Agent lacks the extract privilege"
        )
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
        stored = agent_files.extract_and_index(
            db, agent=agent, local_file_id=local_file_id, pdf_bytes=pdf_bytes
        )
    except ValueError as exc:
        db.commit()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    db.commit()
    db.refresh(stored)
    enqueue_extraction(stored.id)
    return {"file_id": str(stored.id), "status": "extracting"}


@router.post("/teleports/{local_file_id}/reject", status_code=status.HTTP_200_OK)
def reject_teleport(
    local_file_id: str,
    payload: RejectTeleportRequest,
    db: Session = DB_DEP,
    agent: Agent = AGENT_DEP,
) -> dict[str, str | bool]:
    """Reject a pending teleport request; `forever` blocks all future requests for the file."""
    try:
        agent_files.reject_teleport(
            db, agent=agent, local_file_id=local_file_id, forever=payload.forever
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    db.commit()
    return {"local_file_id": local_file_id, "status": "rejected", "blocked": payload.forever}


@router.post("/teleports/{local_file_id}/unblock", status_code=status.HTTP_200_OK)
def unblock_teleport(
    local_file_id: str,
    db: Session = DB_DEP,
    agent: Agent = AGENT_DEP,
) -> dict[str, str]:
    """Clear a reject-forever block so the file can be requested again."""
    try:
        agent_files.unblock_teleport(db, agent=agent, local_file_id=local_file_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    db.commit()
    return {"local_file_id": local_file_id, "status": "unblocked"}


@router.get("/me")
def agent_self(agent: Agent = AGENT_DEP) -> dict:
    """Return the calling agent's identity, approval, and granted privileges (reachability check)."""
    return {
        "agent_id": str(agent.id),
        "name": agent.name,
        "status": agent.status,
        "can_index": agent.can_index,
        "can_extract": agent.can_extract,
        "can_teleport": agent.can_teleport,
        "can_be_requested": agent.can_be_requested,
        "processing_visibility": agent.processing_visibility,
        "server_status_visibility": agent.server_status_visibility,
    }


class SourceRemovedRequest(BaseModel):
    local_file_ids: list[str]


@router.post("/files/source-removed", status_code=status.HTTP_200_OK)
def report_source_removed(
    payload: SourceRemovedRequest,
    db: Session = DB_DEP,
    agent: Agent = AGENT_DEP,
) -> dict[str, int]:
    """The agent reports files whose local source disappeared (kept + flagged server-side)."""
    marked = agent_files.mark_source_removed(db, agent=agent, local_file_ids=payload.local_file_ids)
    db.commit()
    return {"marked": marked}


@router.get("/files", response_model=list[AgentFileStatus])
def list_agent_file_status(db: Session = DB_DEP, agent: Agent = AGENT_DEP) -> list[AgentFileStatus]:
    """Report this agent's files + their processing/teleport state (for the agent's own status view).

    Includes the linked Work's title + authors (#11) so the agent GUI can search/sort by them.
    """
    if not agent.processing_visibility:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Agent lacks processing visibility"
        )
    rows = list(
        db.scalars(
            select(AgentFile).where(AgentFile.agent_id == agent.id).order_by(AgentFile.created_at)
        ).all()
    )
    metadata = agent_files.extracted_metadata_for(db, rows)
    out: list[AgentFileStatus] = []
    for row in rows:
        title, authors = metadata.get(row.id, (None, None))
        out.append(
            AgentFileStatus(
                local_file_id=row.local_file_id,
                virtual_path=row.virtual_path,
                display_path=row.display_path,
                import_action=row.import_action,
                processing_state=row.processing_state,
                teleport_status=row.teleport_status,
                teleport_policy=row.teleport_policy,
                teleport_blocked=row.teleport_blocked,
                extracted_title=title,
                extracted_authors=authors,
            )
        )
    return out


@router.post("/files/{local_file_id}/offer-teleport", status_code=status.HTTP_201_CREATED)
async def offer_teleport(
    local_file_id: str,
    file: UploadFile,
    db: Session = DB_DEP,
    agent: Agent = AGENT_DEP,
) -> dict[str, str]:
    """Agent-*initiated* teleport (#12): the agent pushes a file directly (no prior user request).

    Allowed only when the owner has granted ``can_teleport``. The server verifies the hash, stores
    the managed file + Work, and enqueues extraction — mirroring a user-requested teleport.
    """
    if not agent.can_teleport:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Agent lacks the teleport privilege"
        )
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
        stored = agent_files.offer_teleport(
            db,
            agent=agent,
            local_file_id=local_file_id,
            pdf_bytes=pdf_bytes,
            display_path=file.filename,
        )
    except ValueError as exc:
        db.commit()  # persist any teleport.failed audit/state before surfacing the error
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    db.commit()
    db.refresh(stored)
    enqueue_extraction(stored.id)
    return {"file_id": str(stored.id), "sha256": stored.sha256, "status": "complete"}
