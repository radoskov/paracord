"""Local agent protocol endpoints."""

from fastapi import APIRouter

from app.schemas.agent import AgentManifestRequest, AgentRegisterRequest, AgentRegisterResponse

router = APIRouter()


@router.post("/register", response_model=AgentRegisterResponse)
def register_agent(payload: AgentRegisterRequest) -> AgentRegisterResponse:
    """Register a local workstation agent using a bootstrap token."""
    raise NotImplementedError("Agent registration is not implemented yet.")


@router.post("/manifest")
def receive_manifest(payload: AgentManifestRequest) -> dict[str, str]:
    """Receive a scanned-file manifest from an authenticated agent."""
    raise NotImplementedError("Manifest ingestion is not implemented yet.")


@router.post("/teleport/{agent_file_id}")
def request_teleport(agent_file_id: str) -> dict[str, str]:
    """Create or request a teleport operation for an agent-owned file ID."""
    return {"status": "todo", "agent_file_id": agent_file_id}
